#!/usr/bin/env python3
"""JSONL v4 protocol/security tests. Fresh synthetic vaults only."""
import copy
import contextlib
import io
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
C = runpy.run_path(str(ROOT / "bin/llm_wiki_event_review.py"))
E = runpy.run_path(str(ROOT / "bin/llm-wiki-enrich"), run_name="event_v4_reader_test")
S = runpy.run_path(str(ROOT / "tests/test-event-snapshot-integrity.py"), run_name="event_fixture")
CTX = S["CONTEXT"]
CAPABILITY = "example-synthetic-event-review-capability-only"
NOW = "2026-01-02T00:00:00.000Z"

def pending():
    return S["event"](1, actor={"type":"ai","id":"codex"}, review_status="pending", epistemic_status="ai-proposed",
        target_agents=["codex","opencode"], summary="vfourmarker original", details="Synthetic only 😀", files=[], concepts=[])

def request(target, action="accept", **extra):
    return dict(action=action,target_id=target["id"],expected_revision=C["record_revision"](target),
        request_id="test:"+action,reason="synthetic review",**extra)

def reviewed(target, action="accept", number=2, **extra):
    r=request(target,action,**extra)
    if target.get("supersedes") and action in {"accept","correct"}:
        r["confirm_supersedes"]=True
    return C["prepare"](target,r,actor_id="synthetic-human",can_review=True,now=NOW,event_id=f"{number:016x}")

class VersionedEvents(unittest.TestCase):
    def test_committed_cross_language_vectors(self):
        vectors=json.loads((ROOT/"tests/fixtures/versioned-event-review.json").read_text(encoding="utf-8"))
        self.assertEqual(len(vectors["cases"]),8)
        for case in vectors["cases"]:
            for key in ["record_revision","target_revision","content_revision","policy_revision"]:
                self.assertEqual(C[key](case["event"]),case[key],case["name"])
    def test_pinned_previous_reader_fails_closed_instead_of_ignoring_v4(self):
        old=subprocess.run(["git","show","a0fd743:bin/llm-wiki-enrich"],cwd=ROOT,text=True,capture_output=True,timeout=10)
        if old.returncode: self.skipTest("pinned P2 Markdown reader history unavailable")
        module=types.ModuleType("old_event_review_reader")
        module.__file__=str(ROOT/"bin/llm-wiki-enrich")
        sys.modules[module.__name__]=module
        self.addCleanup(lambda:sys.modules.pop(module.__name__,None))
        exec(compile(old.stdout,module.__file__,"exec"),module.__dict__)
        a=pending();self.write([a,reviewed(a)])
        with self.assertRaises(module.EventSnapshotError) as caught: module._read_event_snapshot(self.root)
        self.assertEqual(caught.exception.code,"unknown-event-schema")
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="wiki-v4-review-")
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/"memory/events").mkdir(parents=True)
        (self.root/"policy").mkdir()
        shutil.copyfile(ROOT/"templates/access-policy.json",self.root/"policy/access.json")
        self.env={k:v for k,v in os.environ.items() if not k.startswith("LLM_WIKI_")}
        self.env.update(LLM_WIKI_ROOT=str(self.root),LLM_WIKI_BIN_TARGET=str(ROOT/"bin"),
            LLM_WIKI_EVENT_REVIEW_CAPABILITY=CAPABILITY,LLM_WIKI_EVENT_REVIEW_ACTOR="synthetic-human")
    def write(self,rows,name="2020-01.jsonl",final_newline=True):
        value="\n".join(json.dumps(e,ensure_ascii=False) for e in rows)+( "\n" if final_newline else "")
        (self.root/"memory/events"/name).write_text(value,encoding="utf-8")
    def ids(self):
        return {e["id"] for e in E["load_events"](self.root,CTX)}
    def bytes(self):
        return {p.name:p.read_bytes() for p in (self.root/"memory/events").glob("*.jsonl")}
    def cli(self,r,success=True,env=None):
        value={"capability":CAPABILITY,**r}
        result=subprocess.run([sys.executable,str(ROOT/"bin/llm-wiki-event-review")],input=json.dumps(value),text=True,capture_output=True,env=env or self.env,timeout=10)
        self.assertEqual(result.returncode,0 if success else 2,result.stderr)
        self.assertNotIn(CAPABILITY,result.stdout+result.stderr)
        return json.loads(result.stdout) if success else result
    def test_accept_correct_withdraw_restore_and_no_ancestor_resurrection(self):
        a=pending(); b=reviewed(a); c=reviewed(b,"correct",3,summary="vfourmarker corrected")
        d=reviewed(c,"withdraw",4); e=reviewed(d,"restore",5)
        for rows,ids in [([a],set()),([a,b],{b["id"]}),([a,b,c],{c["id"]}),([a,b,c,d],set()),([a,b,c,d,e],{e["id"]})]:
            self.write(rows); self.assertEqual(self.ids(),ids)
        self.assertEqual(e["record_id"],a["memory_id"])
    def test_tampering_fails_whole_snapshot_instead_of_reviving_ancestor(self):
        a=pending(); b=reviewed(a); c=reviewed(b,"withdraw",3)
        for field,value in [("summary","tampered"),("details","tampered"),("confidence",0.33),("lifecycle","deprecated"),("target_agents",["*"]),("source","forged")]:
            rows=copy.deepcopy([a,b,c]); rows[-1][field]=value; self.write(rows)
            with self.assertRaises(E["EventSnapshotError"]): self.ids()
        rows=copy.deepcopy([a,b]); rows[0]["summary"]="changed proposal"; self.write(rows)
        with self.assertRaises(E["EventSnapshotError"]): self.ids()
    def test_concept_classification_is_not_approval_and_cas_still_sees_it(self):
        a=pending(); b=reviewed(a); changed=copy.deepcopy(a); changed["concepts"]=["classification"]
        self.assertNotEqual(C["record_revision"](a),C["record_revision"](changed))
        self.write([changed,b]); self.assertEqual(self.ids(),{b["id"]})
        b["concepts"]=["tag"]; self.write([changed,b]); self.assertEqual(self.ids(),{b["id"]})
    def test_fork_missing_parent_scope_and_damaged_binding_fail_closed(self):
        a=pending(); b=reviewed(a); fork=reviewed(a,number=3)
        for rows in [[b],[a,b,fork]]:
            self.write(rows)
            with self.assertRaises(E["EventSnapshotError"]): self.ids()
        for change in [lambda e:e["review"].update(target_revision="sha256:"+"0"*64),lambda e:e["review"].update(actor={"type":"ai","id":"codex"}),lambda e:e.update(project="other")]:
            bad=copy.deepcopy(b);change(bad);self.write([a,bad])
            with self.assertRaises(E["EventSnapshotError"]): self.ids()
    def test_stale_cas_unauthorized_forged_actor_reason_and_invalid_transitions_write_nothing(self):
        a=pending();self.write([a]);before=self.bytes()
        for bad in [dict(request(a),capability="wrong"),dict(request(a),actor={"type":"human"}),dict(request(a),expected_revision="stale"),request(a,"restore")]:
            self.cli(bad,False);self.assertEqual(self.bytes(),before)
        env={k:v for k,v in self.env.items() if k!="LLM_WIKI_EVENT_REVIEW_CAPABILITY"}
        self.cli(request(a),False,env);self.assertEqual(self.bytes(),before)
        b=self.cli(request(a));before=self.bytes()
        self.cli(request(b,"withdraw",reason_override="not allowed"),False)
        r=request(b,"withdraw");r["reason"]="";self.cli(r,False);self.assertEqual(self.bytes(),before)
    def test_persisted_retries_and_late_retry_after_withdrawal(self):
        a=pending();self.write([a]);r=request(a);b=self.cli(r);before=self.bytes()
        self.assertEqual(self.cli(r),b);self.assertEqual(self.bytes(),before)
        d=self.cli(request(b,"withdraw"));self.assertEqual(self.ids(),set());before=self.bytes()
        self.cli(r,False);self.assertEqual(self.bytes(),before)
        self.cli(request(d,"accept"),False);self.assertEqual(self.bytes(),before)
        self.assertEqual(self.cli(request(d,"restore"))["review"]["decision"],"restore")
    def test_legacy_review_and_raw_supersession_cannot_bypass_v4_terminal(self):
        a=pending();self.write([a]);b=self.cli(request(a));d=self.cli(request(b,"withdraw"));before=self.bytes()
        for args in [["--approve",a["id"]],["--supersedes",d["id"],"--project","snapshot-test","new claim"]]:
            result=subprocess.run([sys.executable,str(ROOT/"bin/llm-wiki-event"),*args],env=self.env,text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,2,result.stdout);self.assertEqual(self.bytes(),before)
    def test_correct_requires_explicit_payload_and_inherited_edges_need_confirmation(self):
        a=pending();self.write([a]);b=self.cli(request(a));before=self.bytes()
        self.cli(request(b,"correct"),False)
        self.cli(request(b,"correct",summary="vfourmarker corrected"),False)
        self.assertEqual(self.bytes(),before)
        c=self.cli(request(b,"correct",summary="vfourmarker corrected",confirm_supersedes=True))
        self.assertEqual(self.ids(),{c["id"]})
    def test_inherited_global_acl_cannot_be_silently_confirmed(self):
        a=pending();a["target_agents"]=["*"];self.write([a]);before=self.bytes()
        self.cli(request(a),False);self.assertEqual(self.bytes(),before)
        self.cli(request(a,confirm_global_target=True))
    def test_complete_snapshot_duplicates_and_future_review(self):
        a=pending();b=reviewed(a);self.write([a,b,b]);self.assertEqual(self.ids(),{b["id"]})
        future=C["prepare"](a,request(a),actor_id="synthetic-human",can_review=True,now="2999-01-01T00:00:00.000Z",event_id="0000000000000003")
        self.write([a,future]);self.assertEqual(self.ids(),set())
    def test_no_final_newline_is_repaired_at_append_boundary(self):
        a=pending();name=datetime.now(timezone.utc).strftime("%Y-%m.jsonl");self.write([a],name,False)
        b=self.cli(request(a));self.assertEqual(self.ids(),{b["id"]})
    def test_git_union_normalization_preserves_unicode_and_review_fingerprints(self):
        a=pending();a["details"]="Unicode \u0085 \u2028 \u2029 stays one record";b=reviewed(a)
        self.write([a,b,b]);self.assertEqual(self.ids(),{b["id"]})
        result=subprocess.run([sys.executable,str(ROOT/"bin/llm-wiki-dedupe-events"),"--root",str(self.root)],env=self.env,text=True,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(len(E["_read_event_snapshot"](self.root)),2);self.assertEqual(self.ids(),{b["id"]})
    def test_redaction_and_malformed_input_never_disclose_or_append(self):
        a=pending();self.write([a]);before=self.bytes()
        r=request(a,"correct",summary="example password=example-synthetic-value")
        result=self.cli(r,False);self.assertNotIn("example-synthetic-value",result.stderr);self.assertEqual(self.bytes(),before)
    def test_two_profiles_search_and_restart_observe_correction_and_withdrawal(self):
        a=pending();self.write([a]);b=self.cli(request(a,"correct",summary="vfourmarker replacement"))
        def recall(profile):
            result=subprocess.run([sys.executable,str(ROOT/"bin/llm-wiki-enrich"),"--agent-profile",profile,"--query","vfourmarker","--max-chars","8000"],env=self.env,text=True,capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr);return result.stdout
        for p in ["codex","opencode"]:
            out=recall(p);self.assertIn(b["id"],out);self.assertNotIn(a["id"],out);self.assertIn("replacement",out)
        self.cli(request(b,"withdraw"))
        for p in ["codex","opencode"]: self.assertNotIn("replacement",recall(p))
    def test_real_mcp_profiles_have_no_review_authority_and_observe_changes(self):
        def mcp(profile,name,args):
            calls=[{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}},
                {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":name,"arguments":args}}]
            env={k:v for k,v in self.env.items() if not k.startswith("LLM_WIKI_EVENT_REVIEW_")}
            env["LLM_WIKI_AGENT_PROFILE"]=profile;env["LLM_WIKI_TARGET_AGENTS"]="codex,opencode"
            result=subprocess.run(["node",str(ROOT/"bin/llm-wiki-mcp")],input="".join(json.dumps(c)+"\n" for c in calls),env=env,text=True,capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            return next(json.loads(line) for line in result.stdout.strip().split("\n") if json.loads(line)["id"]==2)
        result=mcp("codex","record_event",{"type":"decision","summary":"mcpeventvfour original","project":"snapshot-test","actor":{"type":"human"},"review_status":"approved"})
        self.assertIn("result",result)
        rows=E["_read_event_snapshot"](self.root);a=C["clean"](rows[0])
        self.assertEqual(a["actor"],{"type":"ai","id":"codex"});self.assertEqual(a["review_status"],"pending")
        for p in ["codex","opencode"]:self.assertNotIn(a["id"],json.dumps(mcp(p,"search_pages",{"query":"mcpeventvfour"})))
        b=self.cli(request(a,"correct",summary="mcpeventvfour corrected"))
        for p in ["codex","opencode"]:self.assertIn(b["id"],json.dumps(mcp(p,"search_pages",{"query":"mcpeventvfour"})))
        self.cli(request(b,"withdraw"))
        for p in ["codex","opencode"]:self.assertNotIn(b["id"],json.dumps(mcp(p,"search_pages",{"query":"mcpeventvfour"})))
        self.assertIn("error",mcp("codex","review_event",request(b,"restore")))
    def test_competing_review_processes_have_one_cas_winner(self):
        a=pending();self.write([a]);requests=[request(a),dict(request(a),request_id="another-review")]
        children=[]
        for r in requests:
            child=subprocess.Popen([sys.executable,str(ROOT/"bin/llm-wiki-event-review")],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=self.env)
            child.stdin.write(json.dumps({**r,"capability":CAPABILITY}));child.stdin.close();child.stdin=None;children.append(child)
        codes=[]
        for child in children:
            out,err=child.communicate(timeout=10);codes.append(child.returncode)
            self.assertNotIn(CAPABILITY,out+err)
        self.assertEqual(sorted(codes),[0,2]);self.assertEqual(len(self.ids()),1)
    def test_unsafe_files_and_malformed_snapshots_do_not_append(self):
        a=pending();self.write([a]);before=self.bytes();outside=self.root/"outside"
        outside.write_text("synthetic unchanged",encoding="utf-8")
        (self.root/"memory/.memory.lock").symlink_to(outside)
        self.cli(request(a),False);self.assertEqual(outside.read_text(),"synthetic unchanged");self.assertEqual(self.bytes(),before)
        (self.root/"memory/.memory.lock").unlink()
        (self.root/"memory/events/broken.jsonl").write_text("{broken\n",encoding="utf-8")
        before=self.bytes();result=self.cli(request(a),False);self.assertNotIn("Traceback",result.stderr);self.assertEqual(self.bytes(),before)
    def test_writer_refuses_physical_line_total_byte_and_entry_overflow(self):
        api=runpy.run_path(str(ROOT/"bin/llm-wiki-event-review"),run_name="bounded_review_cli")
        a=pending();self.write([a]);r={**request(a),"capability":CAPABILITY}
        path=self.root/"memory/events/2020-01.jsonl"
        path.write_bytes(path.read_bytes()+b"\n\n")
        before=self.bytes()
        cases=[{"MAX_EVENT_LINES":3},{"MAX_EVENT_TOTAL_BYTES":len(path.read_bytes())},{"MAX_EVENT_DIRECTORY_ENTRIES":1}]
        for values in cases:
            fake=io.TextIOWrapper(io.BytesIO(json.dumps(r).encode("utf-8")))
            with patch.dict(os.environ,self.env,clear=True),patch.object(sys,"stdin",fake),patch.dict(api["ENGINE"],values):
                with self.assertRaises(ValueError): api["main"]([])
            self.assertEqual(self.bytes(),before)

def golden_vectors():
    a=pending();b=reviewed(a);c=reviewed(b,"correct",3,summary="vfourmarker 修正 café / café / 😀\r\n")
    d=reviewed(c,"withdraw",4);e=reviewed(d,"restore",5)
    classified=copy.deepcopy(c);classified["concepts"]=["标签/类别"]
    extended=copy.deepcopy(a);extended["unknown"]={"😀":None,"\ue000":[True,False,-0.0,1e-7],"嵌套":{"value":12}}
    changed=copy.deepcopy(c);changed["summary"]+="changed"
    entries=[]
    for name,event in [("pending-v3",a),("accepted-v4",b),("corrected-unicode",c),("withdrawn",d),("restored",e),("classification",classified),("unknown-tree-binary64",extended),("tampered-content",changed)]:
        entries.append({"name":name,"event":event,**{key:C[key](event) for key in ["record_revision","target_revision","content_revision","policy_revision"]}})
    return {"schemaVersion":1,"syntheticOnly":True,"cases":entries}

if __name__ == "__main__":
    if sys.argv[1:]==["--print-vectors"]: print(json.dumps(golden_vectors(),ensure_ascii=False,indent=2))
    else: unittest.main(verbosity=2)
