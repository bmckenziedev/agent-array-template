"""JSON Schema validator + the card/template/lanes/profile schemas."""
import copy
import json
import unittest

from .helpers import ENGINE  # noqa: F401  (sys.path)

from factory_engine import schema
from factory_engine.schema import SchemaError, errors, validate_or_raise

TASK = "0123456789abcdef"


def doc_card(**over) -> dict:
    c = {"card": 1, "unit_id": "badge-doc-001", "task_id": TASK, "repo": "toy-shapes", "kind": "doc_map",
         "difficulty": "easy", "target": {"file": "src/badge.js", "symbols": ["formatBadge"]},
         "instruction": "Describe label formatting inputs and the returned display label.", "output": "doc_map_json", "verify": "docs_tsc",
         "retry": {"max_generations": 3}}
    c.update(over)
    return c


class ValidatorTest(unittest.TestCase):
    def check(self, sch: dict, value) -> list[str]:
        schema._check_keywords(sch)
        return schema._collect(value, sch, sch, "")

    def test_types_and_bool_is_not_int(self):
        self.assertEqual(self.check({"type": "integer"}, 3), [])
        self.assertTrue(self.check({"type": "integer"}, True))
        self.assertTrue(self.check({"type": "number"}, False))
        self.assertEqual(self.check({"type": ["string", "null"]}, None), [])

    def test_object_keywords(self):
        s = {"type": "object", "required": ["a"], "additionalProperties": False,
             "properties": {"a": {"type": "string", "pattern": "^x"}}}
        self.assertEqual(self.check(s, {"a": "xy"}), [])
        errs = self.check(s, {"b": 1})
        self.assertTrue(any("missing required property 'a'" in e for e in errs))
        self.assertTrue(any("unexpected property 'b'" in e for e in errs))
        self.assertTrue(self.check(s, {"a": "yx"}))

    def test_arrays_enum_const_bounds(self):
        s = {"type": "array", "items": {"enum": [1, 2]}, "minItems": 1, "maxItems": 2, "uniqueItems": True}
        self.assertEqual(self.check(s, [1, 2]), [])
        self.assertTrue(self.check(s, []))
        self.assertTrue(self.check(s, [1, 1]))
        self.assertTrue(self.check(s, [3]))
        self.assertTrue(self.check({"const": 1}, 2))
        self.assertTrue(self.check({"type": "integer", "maximum": 3}, 4))

    def test_combinators_and_if_then(self):
        s = {"if": {"properties": {"k": {"const": "a"}}, "required": ["k"]},
             "then": {"required": ["x"]}, "else": {"required": ["y"]}}
        self.assertEqual(self.check(s, {"k": "a", "x": 1}), [])
        self.assertTrue(self.check(s, {"k": "a"}))
        self.assertTrue(self.check(s, {"k": "b"}))
        self.assertEqual(self.check({"oneOf": [{"type": "string"}, {"type": "integer"}]}, 3), [])
        self.assertTrue(self.check({"oneOf": [{"type": "number"}, {"type": "integer"}]}, 3))
        self.assertTrue(self.check({"anyOf": [{"type": "string"}]}, 3))
        self.assertTrue(self.check({"not": {"type": "string"}}, "x"))

    def test_refs(self):
        s = {"$defs": {"n": {"type": "integer"}}, "properties": {"a": {"$ref": "#/$defs/n"}}}
        self.assertEqual(self.check(s, {"a": 1}), [])
        self.assertTrue(self.check(s, {"a": "1"}))

    def test_unknown_keyword_refused(self):
        with self.assertRaises(ValueError):
            schema._check_keywords({"type": "object", "dependentRequired": {}})

    def test_all_engine_schemas_load(self):
        for n in ("card.v1", "template.v1", "lanes.v1", "profile.v1"):
            self.assertEqual(schema.load(n)["$schema"], "https://json-schema.org/draft/2020-12/schema")


class CardSchemaTest(unittest.TestCase):
    def test_valid_doc_card(self):
        self.assertEqual(errors("card.v1", doc_card()), [])

    def test_design_narrowing(self):
        bad = {
            "hard difficulty": doc_card(difficulty="hard"),
            "R2 risk": doc_card(risk="R2"),
            "4 generations": doc_card(retry={"max_generations": 4}),
            "kimi on": doc_card(retry={"kimi": True}),
            "doc_map with wrong output": doc_card(output="replace_symbol"),
            "doc_map without symbols": doc_card(target={"file": "src/a.js"}),
            "unknown field": doc_card(extra=1),
            "bad task id": doc_card(task_id="T0142"),
        }
        for why, card in bad.items():
            with self.subTest(why):
                self.assertTrue(errors("card.v1", card), why)

    def test_paths(self):
        for p in ("../x.js", "/abs.js", "C:/x.js", "a/../../b.js", ".git/config", "src/.git/x", "a\\b.js"):
            with self.subTest(p):
                self.assertTrue(errors("card.v1", doc_card(target={"file": p, "symbols": ["f"]})), p)
        for p in ("src/a.js", "lib/x-y/z.cjs", "a..b.js"):
            with self.subTest(p):
                self.assertEqual(errors("card.v1", doc_card(target={"file": p, "symbols": ["f"]})), [], p)

    def test_qnames(self):
        for q in ("Ledger.constructor", "Ledger.get:value", "fn", "$x._y"):
            self.assertEqual(errors("card.v1", doc_card(target={"file": "a.js", "symbols": [q]})), [], q)
        for q in ("a.b.c", "1x", "a b"):
            self.assertTrue(errors("card.v1", doc_card(target={"file": "a.js", "symbols": [q]})), q)

    def test_test_gen_requires_test_file_and_min_tests(self):
        c = doc_card(kind="test_gen", output="new_file", verify="tests")
        errs = errors("card.v1", c)
        self.assertTrue(any("provides" in e for e in errs))
        c.update(provides={"test_file": "src/__tests__/badge.factory.test.js"}, tests={"min_tests": 2})
        self.assertEqual(errors("card.v1", c), [])

    def test_validate_or_raise(self):
        with self.assertRaises(SchemaError) as cm:
            validate_or_raise("card.v1", doc_card(difficulty="hard"), what="card")
        self.assertIn("difficulty", str(cm.exception))


class OtherSchemasTest(unittest.TestCase):
    def test_template(self):
        t = {"template": 1, "template_id": "dm-x", "kind": "doc_map",
             "expand": {"via": "find_undocumented", "repo": "r", "group": {"max_symbols": 4}, "cap": 10}}
        self.assertEqual(errors("template.v1", t), [])
        t2 = copy.deepcopy(t)
        t2["expand"]["group"]["max_symbols"] = 9
        self.assertTrue(errors("template.v1", t2))

    def test_lanes(self):
        ok = {"lanes": {"lane-gpu-a": {"concurrency": 1, "endpoint": {"base_url": "http://lane-gpu-a:8000", "api": "llamacpp"}}},
              "routing": {"doc_map": {"easy": [{"lane": "lane-gpu-a", "temperature": 0.2}]}}}
        self.assertEqual(errors("lanes.v1", ok), [])
        bad = copy.deepcopy(ok)
        bad["routing"]["doc_map"]["easy"] = [{"lane": "lane-gpu-a"}] * 4
        self.assertTrue(errors("lanes.v1", bad), "more than 3 rungs")
        bad2 = copy.deepcopy(ok)
        bad2["lanes"]["lane-gpu-a"]["endpoint"]["base_url"] = "http://user:pw@host:8000"
        self.assertTrue(errors("lanes.v1", bad2), "credentials in a URL")

    def test_default_profile(self):
        p = json.loads((ENGINE / "profiles" / "jsdoc-cjs.json").read_text(encoding="utf-8"))
        self.assertEqual(errors("profile.v1", p), [])


class CrossCheckTest(unittest.TestCase):
    """If the reference `jsonschema` package is installed, it must agree with our validator."""

    def test_agrees_with_jsonschema(self):
        try:
            import jsonschema  # type: ignore
        except ImportError:
            self.skipTest("jsonschema not installed (optional cross-check)")
        sch = schema.load("card.v1")
        jsonschema.Draft202012Validator.check_schema(sch)
        v = jsonschema.Draft202012Validator(sch)
        for c in (doc_card(), doc_card(difficulty="hard"), doc_card(target={"file": "../x.js", "symbols": ["f"]}),
                  doc_card(kind="test_gen", output="new_file", verify="tests")):
            self.assertEqual(bool(list(v.iter_errors(c))), bool(errors("card.v1", c)))


if __name__ == "__main__":
    unittest.main()
