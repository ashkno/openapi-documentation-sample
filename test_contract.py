"""Verify the scoped contract against the unchanged, pinned httpbin application."""
import base64
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

import jsonschema
from openapi_spec_validator import validate
import yaml

REVISION = 'f8ec666b4d1b654e4ff6aedd356f510dcac09f83'
SOURCE = Path(os.environ.get('HTTPBIN_SOURCE', 'reference/httpbin')).resolve()
if subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip() != REVISION:
    raise RuntimeError('Use the documented pinned httpbin revision')
if subprocess.check_output(['git', '-C', str(SOURCE), 'status', '--porcelain', '--untracked-files=no'], text=True).strip():
    raise RuntimeError('The reference source has tracked modifications')
sys.path.insert(0, str(SOURCE))
from httpbin import app

SPEC = yaml.safe_load(Path(__file__).with_name('openapi.yaml').read_text(encoding='utf-8'))
validate(SPEC)
app.config['TESTING'] = True


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def check_response(self, path, response):
        operation = SPEC['paths'][path]['get']
        definition = operation['responses'][str(response.status_code)]
        for name, header in definition.get('headers', {}).items():
            self.assertIn(name, response.headers)
            jsonschema.validate(response.headers[name], header['schema'])
        if 'content' not in definition:
            self.assertEqual(response.data, b'')
            return
        self.assertIn(response.mimetype, definition['content'])
        schema = definition['content'][response.mimetype]['schema']
        body = response.get_json() if response.is_json else response.get_data(as_text=True)
        jsonschema.Draft4Validator(schema, format_checker=jsonschema.FormatChecker()).validate(body)

    def test_scope_is_exactly_three_get_operations(self):
        expected = {'/uuid', '/base64/{value}', '/basic-auth/{user}/{passwd}'}
        self.assertEqual(set(SPEC['paths']), expected)
        for operation in SPEC['paths'].values():
            self.assertEqual(set(operation), {'get'})

    def test_uuid_is_version_four(self):
        response = self.client.get('/uuid')
        self.assertEqual(response.status_code, 200)
        self.check_response('/uuid', response)
        self.assertEqual(uuid.UUID(response.get_json()['uuid']).version, 4)

    def test_decoding_success_and_edge_cases(self):
        cases = {
            'SGVsbG8=': 'Hello',
            'SGVsbG8': 'Incorrect Base64 data try: SFRUUEJJTiBpcyBhd2Vzb21l',
            '_w==': 'Incorrect Base64 data try: SFRUUEJJTiBpcyBhd2Vzb21l',
            '!!!!': '',
            '4pyT': '\u2713',
        }
        for encoded, expected in cases.items():
            with self.subTest(encoded=encoded):
                response = self.client.get('/base64/' + encoded)
                self.assertEqual(response.status_code, 200)
                self.check_response('/base64/{value}', response)
                self.assertEqual(response.get_data(as_text=True), expected)

    def test_authentication_success(self):
        token = base64.b64encode(b'demo:sample').decode('ascii')
        response = self.client.get('/basic-auth/demo/sample', headers={'Authorization': 'Basic ' + token})
        self.assertEqual(response.status_code, 200)
        self.check_response('/basic-auth/{user}/{passwd}', response)
        self.assertEqual(response.get_json(), {'authenticated': True, 'user': 'demo'})

    def test_authentication_missing_or_wrong(self):
        tokens = [None, base64.b64encode(b'demo:wrong').decode('ascii'), base64.b64encode(b'other:sample').decode('ascii')]
        for token in tokens:
            with self.subTest(token=token):
                headers = {} if token is None else {'Authorization': 'Basic ' + token}
                response = self.client.get('/basic-auth/demo/sample', headers=headers)
                self.assertEqual(response.status_code, 401)
                self.check_response('/basic-auth/{user}/{passwd}', response)


if __name__ == '__main__':
    unittest.main(verbosity=2)
