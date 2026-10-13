"""C9 Recipe/Ingredient/CookingStep schema, without upstream's nodeId cutoff."""
import hashlib
import re
from neo4j import GraphDatabase
from langchain_core.documents import Document
from .data_preparation import DataPreparationModule
from .sources import canonical_source


class GraphDataPreparationModule(DataPreparationModule):
    def __init__(self, config, driver=None):
        super().__init__(config.data_path)
        self.config = config
        self.driver = driver or GraphDatabase.driver(config.neo4j_uri,
            auth=(config.neo4j_user, config.neo4j_password),
            max_connection_lifetime=config.neo4j_max_connection_lifetime,
            max_connection_pool_size=config.neo4j_max_connection_pool_size,
            connection_timeout=config.neo4j_connection_timeout)

    def run(self, query, **params):
        with self.driver.session(database=self.config.neo4j_database) as session:
            return session.run(query, **params).data()

    def load_documents(self):
        records = self.run('''MATCH (r:Recipe)
            OPTIONAL MATCH (r)-[:BELONGS_TO_CATEGORY]->(c:Category)
            RETURN elementId(r) AS eid, properties(r) AS props,
                   collect(DISTINCT c.name) AS categories ORDER BY eid''')
        if not records:
            return []
        documents = []
        for record in records:
            props = record['props']
            recipe_id = str(props.get('nodeId') or record['eid'])
            categories = sorted(c for c in record['categories'] if c)
            name = props.get('name') or '未命名食谱'
            content = props.get('content')
            if not content:
                related = self.run('''MATCH (r:Recipe) WHERE elementId(r) = $eid
                    OPTIONAL MATCH (r)-[rel:REQUIRES|CONTAINS_STEP]->(n)
                    RETURN type(rel) AS kind, properties(rel) AS rel, properties(n) AS props
                    ORDER BY coalesce(rel.stepOrder, n.stepNumber, 999), n.name''', eid=record['eid'])
                ingredients, steps = [], []
                for row in related:
                    p, rel = row['props'] or {}, row['rel'] or {}
                    if row['kind'] == 'REQUIRES':
                        ingredients.append(f"* {p.get('name', '')} {rel.get('amount', '')}{rel.get('unit', '')}")
                    elif row['kind'] == 'CONTAINS_STEP':
                        text = f"* {p.get('name', '')} {p.get('description', '')}"
                        for prop, label in [('methods', '方法'), ('tools', '工具'), ('timeEstimate', '时间')]:
                            if p.get(prop):
                                text += f"\n  {label}: {p[prop]}"
                        steps.append(text)
                description = props.get('description', '')
                content = f"# {name}\n{description}\n## 必备原料和工具\n" + '\n'.join(ingredients)
                content += '\n## 操作\n' + '\n'.join(steps)
                for prop, label in [('cuisineType', '菜系'), ('prepTime', '准备时间'), ('cookTime', '烹饪时间')]:
                    if props.get(prop):
                        content += f'\n{label}: {props[prop]}'
            category = categories[0] if categories else props.get('category', '其他')
            difficulty = props.get('difficulty', '未知')
            difficulty = {1: '非常简单', 2: '简单', 3: '中等', 4: '困难', 5: '非常困难'}.get(difficulty, str(difficulty))
            documents.append(Document(page_content=content, metadata={
                'parent_id': recipe_id, 'recipe_id': recipe_id, 'graph_element_id': record['eid'],
                'dish_name': name, 'recipe_name': name, 'category': category,
                'difficulty': difficulty, 'source': canonical_source(props.get('source', f'neo4j:{recipe_id}')),
                'doc_type': 'parent'}))
        self.documents = documents
        return documents

    @staticmethod
    def extract_local_sections(content):
        """Conservative Markdown extraction; no generated relationships or amounts."""
        ingredients, steps = [], []
        section = ''
        for line in content.splitlines():
            if line.startswith('#'):
                section = line.lstrip('#').strip()
                continue
            item = re.match(r'^\s*(?:[-*+]\s+|\d+[.、]\s*)(.+)', line)
            if not item:
                continue
            text = item[1].strip()
            if '原料' in section or '食材' in section:
                # Strip quantities, links and explanatory parentheses from the entity name.
                name = re.split(r'[\d\s（(：:，,]', text, maxsplit=1)[0].strip('。;；')
                if name and not any(c in name for c in '[]`<>') and len(name) <= 20:
                    ingredients.append({'name': name, 'amount': text})
            elif '操作' in section or '步骤' in section:
                steps.append(text)
        return ingredients, steps

    def import_local_recipes(self):
        local = DataPreparationModule(self.config.data_path)
        documents = local.load_documents()
        if not documents:
            raise ValueError('本地食谱为空，无法导入图数据库')
        self.run('CREATE CONSTRAINT recipe_c9_id IF NOT EXISTS FOR (n:LocalRecipeC9) REQUIRE n.nodeId IS UNIQUE')
        self.run('CREATE CONSTRAINT ingredient_c9_id IF NOT EXISTS FOR (n:LocalIngredientC9) REQUIRE n.nodeId IS UNIQUE')
        self.run('CREATE CONSTRAINT step_c9_id IF NOT EXISTS FOR (n:LocalStepC9) REQUIRE n.nodeId IS UNIQUE')
        self.run('CREATE CONSTRAINT category_c9_id IF NOT EXISTS FOR (n:LocalCategoryC9) REQUIRE n.nodeId IS UNIQUE')
        for doc in documents:
            rid = 'local-c9:' + doc.metadata['parent_id']
            ingredients, steps = self.extract_local_sections(doc.page_content)
            # Replace only outgoing relationships owned by this importer. Other graph data is preserved.
            self.run('''MERGE (r:Recipe:LocalRecipeC9 {nodeId:$rid}) SET r += $props
                WITH r OPTIONAL MATCH (r)-[rel]->() WHERE rel.owner = 'local-c9'
                DELETE rel''', rid=rid, props={
                    'name': doc.metadata['dish_name'], 'content': doc.page_content,
                    'category': doc.metadata['category'], 'difficulty': doc.metadata['difficulty'],
                    'source': canonical_source(doc.metadata['source'])})
            category = doc.metadata['category']
            self.run('''MATCH (r:LocalRecipeC9 {nodeId:$rid})
                MERGE (c:Category:LocalCategoryC9 {nodeId:$cid}) SET c.name=$category
                MERGE (r)-[rel:BELONGS_TO_CATEGORY]->(c) SET rel.owner='local-c9' ''',
                rid=rid, cid='local-c9:category:' + category, category=category)
            for ing in ingredients:
                self.run('''MATCH (r:LocalRecipeC9 {nodeId:$rid})
                    MERGE (i:Ingredient:LocalIngredientC9 {nodeId:$iid}) SET i.name=$name
                    MERGE (r)-[rel:REQUIRES]->(i) SET rel.amount=$amount, rel.owner='local-c9' ''',
                    rid=rid, iid='local-c9:ingredient:' + hashlib.sha256(ing['name'].encode()).hexdigest(), **ing)
            for order, step in enumerate(steps, 1):
                self.run('''MATCH (r:LocalRecipeC9 {nodeId:$rid})
                    MERGE (s:CookingStep:LocalStepC9 {nodeId:$sid})
                    SET s.name=$name, s.description=$text, s.stepNumber=$order
                    MERGE (r)-[rel:CONTAINS_STEP]->(s) SET rel.stepOrder=$order, rel.owner='local-c9' ''',
                    rid=rid, sid=f'{rid}:step:{order}', name=f'步骤 {order}', text=step, order=order)
        return len(documents)

    def close(self):
        self.driver.close()

    def upsert_uploaded(self, doc):
        rid = doc.metadata['parent_id']
        if not rid.startswith('upload:'): raise ValueError('Uploaded document ID required')
        ingredients, steps = self.extract_local_sections(doc.page_content)
        self.run('''MERGE (r:Recipe:UploadedRecipeC9 {nodeId:$rid}) SET r += $props
            WITH r OPTIONAL MATCH (r)-[rel]->() WHERE rel.owner='cook-upload' DELETE rel''', rid=rid,
            props={'name':doc.metadata['dish_name'], 'content':doc.page_content, 'source':rid,
                   'category':doc.metadata['category'], 'difficulty':doc.metadata['difficulty']})
        self.run('''MATCH (r:UploadedRecipeC9 {nodeId:$rid}) MERGE (c:Category:LocalCategoryC9 {nodeId:$cid})
            SET c.name=$category MERGE (r)-[rel:BELONGS_TO_CATEGORY]->(c) SET rel.owner='cook-upload' ''',
            rid=rid, cid='local-c9:category:'+doc.metadata['category'], category=doc.metadata['category'])
        for ing in ingredients:
            self.run('''MATCH (r:UploadedRecipeC9 {nodeId:$rid}) MERGE (i:Ingredient:LocalIngredientC9 {nodeId:$iid})
                SET i.name=$name MERGE (r)-[rel:REQUIRES]->(i) SET rel.amount=$amount, rel.owner='cook-upload' ''',
                rid=rid, iid='local-c9:ingredient:'+hashlib.sha256(ing['name'].encode()).hexdigest(), **ing)
        for order, step in enumerate(steps,1):
            self.run('''MATCH (r:UploadedRecipeC9 {nodeId:$rid}) MERGE (s:CookingStep:UploadedStepC9 {nodeId:$sid})
                SET s.name=$name, s.description=$text, s.stepNumber=$order
                MERGE (r)-[rel:CONTAINS_STEP]->(s) SET rel.stepOrder=$order, rel.owner='cook-upload' ''',
                rid=rid, sid=f'{rid}:step:{order}', name=f'步骤 {order}', text=step, order=order)

    def remove_failed_upload(self, rid):
        if not rid.startswith('upload:'): raise ValueError('Uploaded document ID required')
        self.run('MATCH (r:UploadedRecipeC9 {nodeId:$rid}) DETACH DELETE r', rid=rid)
        self.run('MATCH (s:UploadedStepC9) WHERE s.nodeId STARTS WITH $prefix DETACH DELETE s', prefix=rid+':step:')
