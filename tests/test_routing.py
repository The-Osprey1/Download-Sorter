import pytest
from pathlib import Path
import sys
import copy

root = Path(__file__).resolve().parent.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from sorter import evaluate_routing, matches_keyword, explain_classification, load_config

@pytest.fixture
def base_config():
    return load_config()

def test_keyword_boundary_matching():
    assert matches_keyword('IME3311_Exam.pdf', 'ime3311') is True
    assert matches_keyword('lecture_ime 3311_notes.docx', 'ime 3311') is True
    assert matches_keyword('ime-3311-assignment.pdf', 'ime-3311') is True
    assert matches_keyword('solidcast_tutorial.pdf', 'solidcast') is True
    assert matches_keyword('dime33110_notes.pdf', 'ime3311') is False

def test_routing_contract_keys(base_config):
    res = evaluate_routing('sample.pdf', base_config)
    assert 'destination' in res
    assert 'match_type' in res
    assert 'winning_rule' in res
    assert 'matched_trigger' in res
    assert 'is_active_project' in res
    assert 'sniffer_details' in res
    assert 'rule_trace' in res
    assert isinstance(res['rule_trace'], list)

def test_ignore_rules(base_config):
    res_cr = evaluate_routing('big_download.iso.crdownload', base_config)
    assert res_cr['match_type'] == 'ignored'
    assert res_cr['destination'] == ''

    res_lock = evaluate_routing('~$WordDocument.docx', base_config)
    assert res_lock['match_type'] == 'ignored'

    res_ini = evaluate_routing('desktop.ini', base_config)
    assert res_ini['match_type'] == 'ignored'

def test_keyword_rule_precedence_and_extension_restriction(base_config):
    res = evaluate_routing('IME3311_LabReport.pdf', base_config, active_project=None)
    assert res['match_type'] == 'keyword'
    assert res['destination'] == 'Documents\\School\\IME 3311'
    assert res['is_active_project'] is False

    res2 = evaluate_routing('homework_assignment_1.docx', base_config, active_project=None)
    assert res2['match_type'] == 'keyword'
    assert res2['destination'] == 'Documents\\School'

    res3 = evaluate_routing('ime3311_gear.stl', base_config, active_project=None)
    assert res3['match_type'] == 'extension'
    assert res3['destination'] == '3D Models\\STL'

def test_rule_enabled_toggle(base_config):
    cfg = copy.deepcopy(base_config)
    cfg['keyword_rules'][0]['enabled'] = False
    res = evaluate_routing('IME3311_LabReport.pdf', cfg, active_project=None)
    assert res['winning_rule'] != 'IME 3311 Coursework'
    assert res['destination'] == 'Documents\\PDFs'

    for r in cfg['extension_rules']:
        if '.pdf' in r.get('extensions', []):
            r['enabled'] = False
    res_fallback = evaluate_routing('unmatched.pdf', cfg, active_project=None)
    assert res_fallback['match_type'] == 'fallback'
    assert res_fallback['destination'] == 'Other'

def test_reordered_rules_precedence(base_config):
    cfg = copy.deepcopy(base_config)
    rule_a = {'name': 'Projects', 'folder': 'Documents\\Projects', 'match': ['project'], 'extensions': ['.pdf'], 'enabled': True}
    rule_b = {'name': 'Robotics', 'folder': 'Documents\\Robotics', 'match': ['robotics'], 'extensions': ['.pdf'], 'enabled': True}

    cfg['keyword_rules'] = [rule_a, rule_b]
    res1 = evaluate_routing('robotics_project.pdf', cfg)
    assert res1['winning_rule'] == 'Projects'
    assert res1['destination'] == 'Documents\\Projects'

    cfg['keyword_rules'] = [rule_b, rule_a]
    res2 = evaluate_routing('robotics_project.pdf', cfg)
    assert res2['winning_rule'] == 'Robotics'
    assert res2['destination'] == 'Documents\\Robotics'

def test_explain_classification_parity(base_config, tmp_path):
    test_file = tmp_path / 'IME3311_Exam.pdf'
    test_file.write_text('dummy content', encoding='utf-8')
    folder, reason = explain_classification(test_file, base_config)
    res = evaluate_routing(test_file.name, base_config, file_path=test_file)
    assert folder == res['destination']
    assert reason == res['matched_trigger']
