"""Static contracts for the CRUD demo. Integration tests use ephemeral PostgreSQL in workflow 05."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class CrudContractTests(unittest.TestCase):
    def test_backend_compiles_and_keeps_health_endpoints(self):
        source = (ROOT / 'apps/backend/app.py').read_text()
        tree = ast.parse(source)
        for name in ('health', 'db_health', 'version', 'create_record',
                     'list_records', 'update_record', 'delete_record'):
            self.assertTrue(any(isinstance(n, ast.FunctionDef) and n.name == name
                                for n in tree.body))

    def test_four_tables_and_no_destructive_schema_statements(self):
        schema = (ROOT / 'db/schema.sql').read_text().upper()
        for table in ('CATEGORIES', 'PRODUCTS', 'CUSTOMERS', 'ORDERS'):
            self.assertIn('CREATE TABLE IF NOT EXISTS ' + table, schema)
        for forbidden in ('DROP TABLE', 'TRUNCATE', 'DROP DATABASE', 'DELETE FROM'):
            self.assertNotIn(forbidden, schema)

    def test_frontend_contract(self):
        html = (ROOT / 'apps/frontend/index.html').read_text()
        for phrase in ('MeowCloud Elastic Infrastructure', '/api/', 'meow_demo_token',
                       'categories', 'products', 'customers', 'orders'):
            self.assertIn(phrase, html)

    def test_bootstrap_requires_explicit_confirmation(self):
        script = (ROOT / 'scripts/bootstrap_meowcrud.py').read_text()
        self.assertIn('CREATE_MEOWCLOUD_DEMO_DB', script)
        self.assertIn('initialized', script)


if __name__ == '__main__':
    unittest.main()
