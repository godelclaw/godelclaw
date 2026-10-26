"""Static authored-form checks, not a runtime parser or semantic oracle."""
import json
from pathlib import Path
import re
import unittest

ROOT=Path(__file__).resolve().parents[1]
NATIVE=('effect_receipt_values_cetta.metta','effect_receipt_io_cetta.metta',
        'public_worklog_cetta.metta','effect_receipts_cetta.metta','effect_receipts_native.metta')


def forms(source):
    stack=[];output=[]
    for token in re.findall(r'"(?:\\.|[^"\\])*"|;[^\n]*|[()]|[^\s()]+',source):
        if token.startswith(';'):continue
        if token=='(':stack.append([])
        elif token==')':
            assert stack,'extra closing parenthesis'
            value=stack.pop();(stack[-1] if stack else output).append(value)
        else:(stack[-1] if stack else output).append(token)
    assert not stack,'unclosed authored form'
    return output


class SourceTest(unittest.TestCase):
    def test_balanced_forms_and_control_arities(self):
        def walk(value):
            if not isinstance(value,list):return
            if value and isinstance(value[0],str) and value[0] in ('if','let','let*','case','='):
                self.assertEqual(len(value),{'if':4,'let':4,'let*':3,'case':3,'=':3}[value[0]],value[:2])
            for child in value:walk(child)
        for name in NATIVE:
            with self.subTest(name=name):
                for value in forms((ROOT/'src'/name).read_text()):walk(value)

    def test_native_imports_are_packaged(self):
        selected=set(json.loads((ROOT/'config/runtime-files.json').read_text()))
        for name in (*NATIVE,'effect_receipts.metta','effect_receipts_python.metta'):
            self.assertIn('src/'+name,selected)
            for imported in re.findall(r'import! &self \./(src/[^\s)]+)',(ROOT/'src'/name).read_text()):
                self.assertIn(imported,selected)

    def test_native_does_not_call_ported_python(self):
        source=(ROOT/'src/effect_receipts_cetta.metta').read_text()
        self.assertNotRegex(source,r'py-call\s+\((effect_receipts|public_worklog)\.')
        self.assertIn('effect_receipts_plugin.status',source)
        self.assertIn('effect_receipts_plugin.add',source)
        self.assertIn('stream_sink.receipt',source)


if __name__=='__main__':unittest.main()
