#!/usr/bin/env python3
"""Offline tests for modules/seccomp-gvisor/seccomp_profile.py and the shipped profile (no cluster needed).

    python3 modules/seccomp-gvisor/tests/test_profile.py
"""
import copy
import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import seccomp_profile as prof  # noqa: E402  (modules/seccomp-gvisor/seccomp_profile.py)

REF = prof.load(prof.REFERENCE)
SHIPPED = prof.load(prof.SHIPPED)


def names(spec, action):
    return {n for r in spec["syscalls"] if r["action"] == action for n in r["names"]}


class ShippedProfile(unittest.TestCase):
    def test_imported_the_right_module(self):
        self.assertTrue(hasattr(prof, "build"), "wrong module imported")

    def test_committed_file_is_the_build_output(self):
        # profiles/ must be regenerated (seccomp_profile.py build -o ...) whenever reference/ or the
        # builder changes; byte-equal, LF endings.
        self.assertEqual(prof.SHIPPED.read_bytes(), prof.dumps(prof.build(REF)).encode())

    def test_at_least_as_strict_as_runtime_default(self):
        self.assertEqual(prof.check(SHIPPED, REF), [])

    def test_exactly_three_changes(self):
        self.assertEqual(prof.diff(SHIPPED, REF), [
            "- ptrace: allowed in reference, default action (SCMP_ACT_ERRNO) here",
            "~ seccomp: arg filters differ",
            "~ clone3: SCMP_ACT_TRACE here, SCMP_ACT_ERRNO in reference",
            "~ clone3: errnoRet None here, 38 in reference",
        ])

    def test_seccomp_allowed_only_without_listener_flag(self):
        # TRACE is outranked by USER_NOTIF in a Linux filter stack, so a process must not be able
        # to install a listener filter (measured under runc: clone3 then runs unfiltered)
        rules = [r for r in SHIPPED["syscalls"] if "seccomp" in r["names"]]
        self.assertEqual(rules, [{"action": "SCMP_ACT_ALLOW", "names": ["seccomp"],
                                  "args": [{"index": 1, "op": "SCMP_CMP_MASKED_EQ", "value": 8}]}])
        self.assertTrue(prof.listener_blocked(SHIPPED))
        self.assertFalse(prof.listener_blocked(REF))

    def test_clone3_traced_and_ptrace_denied(self):
        self.assertIn("clone3", names(SHIPPED, "SCMP_ACT_TRACE"))
        self.assertNotIn("clone3", names(SHIPPED, "SCMP_ACT_ALLOW"))
        self.assertNotIn("ptrace", names(SHIPPED, "SCMP_ACT_ALLOW"))
        # the rest of the debugger group is unchanged from RuntimeDefault
        self.assertTrue({"process_vm_readv", "process_vm_writev"} <= names(SHIPPED, "SCMP_ACT_ALLOW"))

    def test_namespace_and_mount_calls_still_refused(self):
        allowed = names(SHIPPED, "SCMP_ACT_ALLOW")
        for sc in ("unshare", "setns", "mount", "umount2", "pivot_root", "keyctl", "bpf",
                   "open_by_handle_at", "init_module", "kexec_load", "reboot", "swapon"):
            self.assertNotIn(sc, allowed, sc)
        clone = [r for r in SHIPPED["syscalls"] if r["names"] == ["clone"]]
        self.assertEqual(len(clone), 1)
        self.assertEqual(clone[0]["args"][0]["op"], "SCMP_CMP_MASKED_EQ")
        self.assertEqual(clone[0]["args"][0]["value"], 0x7E020000)   # CLONE_NEW* flags must be 0

    def test_default_action_and_arches_unchanged(self):
        self.assertEqual(SHIPPED["defaultAction"], "SCMP_ACT_ERRNO")
        self.assertNotIn("defaultErrnoRet", SHIPPED)
        self.assertEqual(SHIPPED["architectures"], REF["architectures"])


class Checker(unittest.TestCase):
    """The checker must catch every way a profile could be looser than RuntimeDefault."""

    def bad(self, spec):
        return prof.check(spec, REF)

    def test_reference_passes(self):
        self.assertEqual(self.bad(REF), [])

    def test_allow_clone3_rejected(self):
        self.assertTrue(self.bad(prof.build(REF, clone3="allow", keep_ptrace=True)))

    def test_trace_with_ptrace_rejected(self):
        out = self.bad(prof.build(REF, clone3="trace", keep_ptrace=True))
        self.assertTrue(any("ptrace is allowed" in b for b in out), out)

    def test_trace_with_listener_rejected(self):
        # the profile as first shipped (a740314d): TRACE + ptrace denied, seccomp(2) unfiltered
        out = self.bad(prof.build(REF, clone3="trace", keep_listener=True))
        self.assertTrue(any("listener" in b for b in out), out)

    def test_listener_rule_must_really_mask_the_flag(self):
        for args in ([{"index": 0, "op": "SCMP_CMP_MASKED_EQ", "value": 8}],      # wrong argument
                     [{"index": 1, "op": "SCMP_CMP_MASKED_EQ", "value": 1}],      # wrong bit
                     [{"index": 1, "op": "SCMP_CMP_EQ", "value": 0}],             # not a mask test
                     [{"index": 1, "op": "SCMP_CMP_MASKED_EQ", "value": 8, "valueTwo": 8}]):   # requires the flag
            p = copy.deepcopy(SHIPPED)
            for r in p["syscalls"]:
                if r["names"] == ["seccomp"]:
                    r["args"] = args
            self.assertTrue(any("listener" in b for b in self.bad(p)), args)
        p = copy.deepcopy(SHIPPED)
        p["syscalls"].append({"names": ["seccomp"], "action": "SCMP_ACT_ALLOW"})  # second, unconditional rule
        self.assertTrue(any("listener" in b for b in self.bad(p)))

    def test_without_trace_no_listener_rule_needed(self):
        # RuntimeDefault itself (ERRNO) is not affected: ERRNO outranks USER_NOTIF
        self.assertEqual(self.bad(prof.build(REF, clone3="errno", keep_ptrace=True, keep_listener=True)), [])

    def test_extra_syscall_rejected(self):
        p = copy.deepcopy(SHIPPED)
        p["syscalls"].append({"names": ["unshare"], "action": "SCMP_ACT_ALLOW"})
        self.assertTrue(any(b.startswith("unshare is allowed") for b in self.bad(p)))

    def test_widened_args_rejected(self):
        p = copy.deepcopy(SHIPPED)
        p["syscalls"].append({"names": ["clone"], "action": "SCMP_ACT_ALLOW"})   # no flag filter
        self.assertTrue(any(b.startswith("clone allowed under args") for b in self.bad(p)))

    def test_default_allow_rejected(self):
        p = copy.deepcopy(SHIPPED)
        p["defaultAction"] = "SCMP_ACT_ALLOW"
        self.assertTrue(any("defaultAction" in b for b in self.bad(p)))

    def test_log_action_rejected(self):
        p = copy.deepcopy(SHIPPED)
        p["syscalls"].append({"names": ["mount"], "action": "SCMP_ACT_LOG"})
        self.assertTrue(any(b.startswith("mount is allowed") for b in self.bad(p)))

    def test_listener_rejected(self):
        p = copy.deepcopy(SHIPPED)
        p["listenerPath"] = "/run/x.sock"
        self.assertTrue(any("listenerPath" in b for b in self.bad(p)))

    def test_errno_variant_is_runtime_default(self):
        # option (a) as first written: identical to RuntimeDefault (and EPERM under runsc)
        self.assertEqual(prof.diff(prof.build(REF, clone3="errno", keep_ptrace=True, keep_listener=True), REF), [])


class Reference(unittest.TestCase):
    def test_reference_is_containerd_drop_all(self):
        # containerd adds cap-gated rules (e.g. unshare/mount with CAP_SYS_ADMIN) only when a
        # container keeps those capabilities; the reference must be the drop-ALL variant.
        self.assertEqual(REF["syscalls"][-1], {"action": "SCMP_ACT_ERRNO", "errnoRet": 38, "names": ["clone3"]})
        self.assertNotIn("unshare", names(REF, "SCMP_ACT_ALLOW"))
        self.assertEqual(sum(len(r["names"]) for r in REF["syscalls"]), 378)

    def test_json_stable(self):
        self.assertEqual(json.loads(prof.dumps(SHIPPED)), SHIPPED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
