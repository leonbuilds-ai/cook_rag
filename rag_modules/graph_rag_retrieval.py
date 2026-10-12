"""Bounded, parameterized graph retrieval with real relationship evidence."""
import re
from langchain_core.documents import Document
from .retrieval_optimization import matches


class GraphRAGRetrieval:
    def __init__(self, config, data_module):
        self.config = config
        self.data_module = data_module
        self.parents = {d.metadata['graph_element_id']: d for d in data_module.documents}
        self.entities = data_module.run('''MATCH (n) WHERE n:Recipe OR n:Ingredient OR n:Category
            RETURN DISTINCT n.name AS name, labels(n) AS labels ORDER BY name''')

    def extract_entities(self, query):
        found = {'recipes': [], 'ingredients': [], 'categories': [], 'excluded': []}
        for row in self.entities:
            name = row['name']
            if not name or name not in query:
                continue
            if 'Ingredient' in row['labels']:
                negative = re.search(r'(?:不放|不要|不含|不吃|不能吃|排除|过敏)(?:的|有)?' + re.escape(name), query)
                negative = negative or re.search(re.escape(name) + r'(?:过敏|不能吃)', query)
                found['excluded' if negative else 'ingredients'].append(name)
            if 'Recipe' in row['labels']:
                found['recipes'].append(name)
            if 'Category' in row['labels']:
                found['categories'].append(name)
        for key in found:
            found[key] = sorted(set(n for n in found[key]
                if not any(n != other and n in other for other in found[key])))
        return found

    def graph_rag_search(self, query, top_k=5, filters=None):
        entities = self.extract_entities(query)
        recipes, ingredients = entities['recipes'], entities['ingredients']
        categories, excluded = entities['categories'], entities['excluded']
        if not any(entities.values()):
            return []
        expand = any(word in query for word in ('相似', '类似', '共同', '搭配', '关系'))
        require_all = len(ingredients) > 1 and any(w in query for w in ('和', '同时', '都有', '一起'))
        # Depth is config-validated; all user text remains parameters. No generated Cypher.
        query_text = '''MATCH (r:Recipe)
            WHERE (size($recipes)=0 OR r.name IN $recipes)
              AND (size($ingredients)=0 OR
                ($require_all AND ALL(name IN $ingredients WHERE EXISTS {
                    MATCH (r)-[:REQUIRES]->(i:Ingredient) WHERE i.name=name })) OR
                (NOT $require_all AND EXISTS {
                    MATCH (r)-[:REQUIRES]->(i:Ingredient) WHERE i.name IN $ingredients }))
              AND (size($categories)=0 OR r.category IN $categories OR EXISTS {
                    MATCH (r)-[:BELONGS_TO_CATEGORY]->(c:Category) WHERE c.name IN $categories })
              AND NOT EXISTS { MATCH (r)-[:REQUIRES]->(i:Ingredient) WHERE i.name IN $excluded }
            RETURN elementId(r) AS eid, r.name AS name ORDER BY name'''
        rows = self.data_module.run(query_text, recipes=recipes, ingredients=ingredients,
                                   categories=categories, excluded=excluded, require_all=require_all)
        if expand and recipes and self.config.max_graph_depth >= 2:
            rows += self.data_module.run('''MATCH (source:Recipe)-[:REQUIRES]->(i:Ingredient)<-[:REQUIRES]-(r:Recipe)
                WHERE source.name IN $recipes AND r <> source
                  AND NOT EXISTS { MATCH (r)-[:REQUIRES]->(bad:Ingredient) WHERE bad.name IN $excluded }
                WITH r, count(DISTINCT i) AS common
                RETURN elementId(r) AS eid, r.name AS name ORDER BY common DESC, name LIMIT $limit''',
                recipes=recipes, excluded=excluded, limit=top_k * 3)
        results, seen = [], set()
        for row in rows:
            doc = self.parents.get(row['eid'])
            if not doc or row['eid'] in seen or not matches(doc, filters):
                continue
            seen.add(row['eid'])
            evidence = self.data_module.run('''MATCH (r:Recipe)-[rel:REQUIRES|BELONGS_TO_CATEGORY]->(n)
                WHERE elementId(r)=$eid
                RETURN r.name AS source, type(rel) AS relation, n.name AS target ORDER BY relation, target''',
                eid=row['eid'])
            paths = [f"{e['source']} --{e['relation']}--> {e['target']}" for e in evidence]
            results.append(Document(page_content=doc.page_content,
                metadata={**doc.metadata, 'search_type': 'graph', 'graph_evidence': paths}))
            if len(results) >= top_k:
                break
        return results
