import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from nl2sql_template_core.profiles import apply_profile_env
apply_profile_env('qwen38_p0', force=True)
from nl2sql_template_core.knowledge_boundary import GenerationBoundary, ALLOWED_NAMES
from nl2sql_template_core.lifecycle import build_value_probe, validate_readonly_sql
from nl2sql_template_core.quality_contracts import compare_result_values
from nl2sql_template_core.query_evidence import literal_name_facts, expand_verified_name_schema, attach_schema_metadata
from nl2sql_template_core.validation import _static_validate_sql
from nl2sql_template_core.domain import _merge_required_tables

DDL = '''CREATE TABLE sample (
 id INT PRIMARY KEY,
 label_name VARCHAR(80) COMMENT '名称'
) ENGINE=InnoDB COMMENT='样本';
CREATE TABLE other (
 id INT PRIMARY KEY,
 label_name VARCHAR(80) COMMENT '名称'
) ENGINE=InnoDB COMMENT='其他';'''

class P0Tests(unittest.TestCase):
    def test_boundary_blocks_extra_knowledge_and_gt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'knowledge';root.mkdir()
            for name in ALLOWED_NAMES: (root/name).write_text('[]')
            gt=Path(tmp)/'answer.xlsx';gt.write_text('secret')
            extra=Path(tmp)/'other/knowledge/templates.json';extra.parent.mkdir(parents=True);extra.write_text('{}')
            with GenerationBoundary(root, gt) as guard:
                self.assertEqual((root/'qs.json').read_text(), '[]')
                with self.assertRaises(PermissionError):gt.read_bytes()
                with self.assertRaises(PermissionError):extra.read_text()
            self.assertEqual(len(guard.reads),1)
            self.assertEqual(gt.read_text(),'secret')
    def test_exactly_four_files(self):
        root=Path(__file__).resolve().parents[1]/'portable_template_llm_module/data/knowledge'
        self.assertEqual({p.name for p in root.iterdir()}, ALLOWED_NAMES)
    def test_probe_parameterization(self):
        schema={'tables':{'sample':{'columns':{'label_name':{'type':'VARCHAR(80)'}}}}}
        sql,params=build_value_probe('sample','label_name',"a' OR 1=1",schema,mode='exact')
        self.assertNotIn("a' OR",sql);self.assertEqual(params[0],"a' OR 1=1")
        with self.assertRaises(ValueError): build_value_probe('unknown','label_name','x',schema)
    def test_no_implicit_archive_filter_and_exact_spelling(self):
        schema={'tables':{'sample':{'columns':{'label_name':{'type':'VARCHAR(80)'}, 'IsArchive':{'type':'INT'}}}}}
        calls=[]
        def probe(sql,params):
            calls.append((sql,params));return [{'value':'产品名称(甲)'},{'value':'产品名称(乙)'}]
        facts=literal_name_facts('查询产品名称(甲)',schema,['sample'],probe)
        self.assertEqual(len(facts),1);self.assertNotIn('IsArchive',calls[0][0])
        self.assertEqual(calls[0][1],('查询产品名称(甲)',))
    def test_verified_name_can_add_actual_candidate(self):
        state={'query':'查询产品名称(甲)','full_db_schema':DDL,'domain_schema':json.dumps({'tables':[{'name':'sample','columns':['id','label_name']}]})}
        values=[{'verified':True,'verified_value':'产品名称(甲)','field':'other.label_name','match':'stored full name occurs verbatim in user request'}]
        result=expand_verified_name_schema(state,values,['other'])
        self.assertEqual({t['name'] for t in result['tables']},{'sample','other'})
        values[0]['verified']=False
        self.assertIsNone(expand_verified_name_schema(state,values,['other']))
    def test_feedback_retention(self):
        result=_merge_required_tables({'tables':[{'name':'sample'}]},['other'])
        self.assertEqual({t['name'] for t in result['tables']},{'sample','other'})
    def test_scope_validation_and_readonly(self):
        state={'domain_schema':json.dumps({'tables':[{'name':'sample','columns':['id','label_name']}]})}
        self.assertFalse(_static_validate_sql(state,'WITH t AS (SELECT id FROM sample) SELECT id FROM t'))
        self.assertTrue(_static_validate_sql(state,'SELECT id FROM missing'))
        self.assertTrue(_static_validate_sql(state,'SELECT missing FROM sample'))
        for sql in ['DELETE FROM sample','SELECT 1; SELECT 2','SELECT SLEEP(10)','SELECT id FROM sample FOR UPDATE']:
            self.assertTrue(validate_readonly_sql(sql))
    def test_all_llm_generation_and_repair_entrypoints(self):
        from langchain_core.runnables import RunnableLambda
        from langchain_core.messages import AIMessage
        from nl2sql_template_core import generation
        fake = RunnableLambda(lambda prompt, **kwargs: AIMessage(content='SELECT 1 AS value'))
        state = {'query': '统计记录数量', 'generated_sql': 'SELECT missing FROM sample',
                 'validation_errors': ['Unknown column'], 'precheck_error_message': 'Unknown column',
                 'error_message': 'Unknown column', 'result_check_notes': 'Output mismatch'}
        with patch.object(generation, 'llm', fake):
            for fn in (generation.generate_sql_from_plan, generation.repair_sql,
                       generation.repair_sql_after_execution, generation.repair_sql_after_result_check):
                with self.subTest(entrypoint=fn.__name__):
                    result=fn(state)
                    self.assertIn('SELECT 1', result['generated_sql'])

    def test_strict_values_not_numeric_only(self):
        self.assertTrue(compare_result_values([{'a':'x','b':None}],[{'c':'x','d':None}]))
        self.assertFalse(compare_result_values([{'a':'x'}],[{'a':'y'}]))
        self.assertFalse(compare_result_values([{'a':1},{'a':1}],[{'a':1}]))
        self.assertFalse(compare_result_values([{'a':None}],[{'a':0}]))

if __name__=='__main__': unittest.main()
