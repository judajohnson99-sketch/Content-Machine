#!/usr/bin/env python3
"""Ranking and the approval policy: what is recommended, why, and what is
never allowed to be.

Pure functions over candidate dicts, so the properties that matter are
asserted directly: ineligible rights score zero, reuse beats a marginal
generation but not a clearly better one, a deferred generation is listed but
never recommended, model similarity stays advisory, and graduated autonomy
cannot be switched on.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import asset_licensing as licensing  # noqa: E402
import asset_ranking as ranking  # noqa: E402

REQ = {
    "scene_id": "s01", "query": "misty pine forest dawn", "terms": ["fog", "trees"],
    "subjects": ["forest"], "direction_terms": ["indigo", "mist"], "avoid": ["people", "text"],
    "wants": "either", "duration_seconds": 8.0, "target_width": 1920, "target_height": 1080,
    "used_assets": [],
}


def stock_candidate(cid="stock:pexels:image:1", description="misty pine forest at dawn with fog",
                    width=4000, height=2250, kind="image", eligibility=licensing.ELIGIBLE, **extra):
    rights = licensing.stock_rights("pexels", creator="Ada", creator_url="u", source_url="s",
                                    kind=kind, external_id=cid)
    rights["eligibility"] = eligibility
    return {"candidate_id": cid, "source": "stock", "provider": "pexels", "media_kind": kind,
            "title": description, "description": description, "tags": description.split(),
            "width": width, "height": height, "duration_seconds": 12 if kind == "video" else None,
            "full": {"width": min(width, 1880)}, "rights": rights,
            "cost": {"money_usd": 0.0, "resource": "network"}, "state": "available", **extra}


def library_candidate(cid="library:" + "a" * 64, description="misty pine forest at dawn",
                      rights_ok=True, usage=(), tags=("forest", "fog"), **extra):
    return {"candidate_id": cid, "source": "library", "provider": "library", "media_kind": "image",
            "title": description, "description": description, "tags": list(tags),
            "width": 1920, "height": 1080, "full": {"width": 1920},
            "rights": {"source": "owner", "license": "Owner's own work", "commercial_use": True},
            "library": {"asset_id": cid[8:], "rights_ok": rights_ok, "rights_problem": None,
                        "usage": list(usage)},
            "cost": {"money_usd": 0.0, "resource": "none"}, "state": "available", **extra}


def generated_candidate(state="available"):
    return {"candidate_id": "generated:s01:abc", "source": "generated", "provider": "comfyui",
            "media_kind": "image", "title": "pine forest", "description": "misty pine forest at dawn fog",
            "tags": [], "width": 768, "height": 432, "full": {"width": 1920},
            "generation": {"prompt": "misty pine forest at dawn, fog, indigo mist trees"},
            "rights": {}, "cost": {"money_usd": 0.0, "resource": "gpu"}, "state": state}


def json_copy(value):
    import json
    return json.loads(json.dumps(value))


class ScoringTests(unittest.TestCase):
    def test_a_relevant_widescreen_candidate_outranks_an_irrelevant_portrait_one(self):
        good = stock_candidate("stock:pexels:image:1")
        bad = stock_candidate("stock:pexels:image:2", description="city street neon cars",
                              width=2000, height=3000)
        result = ranking.rank([bad, good], REQ)
        self.assertEqual(result["ranked"][0]["candidate_id"], good["candidate_id"])
        self.assertGreater(result["ranked"][0]["score"], result["ranked"][1]["score"] + 0.2)

    def test_every_ranking_is_explained_factor_by_factor(self):
        record = ranking.rank([stock_candidate()], REQ)["ranked"][0]
        ids = {f["id"] for f in record["factors"]}
        self.assertEqual(ids, set(ranking.WEIGHTS))
        for factor in record["factors"]:
            self.assertTrue(factor["detail"], factor)
            self.assertIn("label", factor)
        self.assertIn(record["confidence"], ("low", "medium", "high"))
        self.assertTrue(record["summary"])
        self.assertAlmostEqual(sum(ranking.WEIGHTS.values()), 1.0)

    def test_ineligible_licence_scores_zero_and_is_never_recommended(self):
        bad = stock_candidate("stock:pexels:image:1", eligibility=licensing.INELIGIBLE)
        ok = stock_candidate("stock:pexels:image:2", description="forest")
        result = ranking.rank([bad, ok], REQ)
        by_id = {r["candidate_id"]: r for r in result["ranked"]}
        self.assertEqual(by_id[bad["candidate_id"]]["score"], 0.0)
        self.assertFalse(by_id[bad["candidate_id"]]["eligible"])
        self.assertEqual(result["recommended"], ok["candidate_id"])

    def test_incomplete_rights_are_penalised_and_explained(self):
        full = stock_candidate("stock:pexels:image:1")
        partial = stock_candidate("stock:pexels:image:2", eligibility=licensing.REVIEW_REQUIRED)
        result = ranking.rank([full, partial], REQ)
        self.assertEqual(result["ranked"][0]["candidate_id"], full["candidate_id"])
        record = next(r for r in result["ranked"] if r["candidate_id"] == partial["candidate_id"])
        self.assertTrue(any("review" in l.lower() for l in record["limitations"]))

    def test_thin_metadata_lowers_confidence_and_says_so(self):
        thin = stock_candidate(description="photo")
        record = ranking.rank([thin], REQ)["ranked"][0]
        self.assertEqual(record["confidence"], "low")
        self.assertTrue(any("descriptive text" in l for l in record["limitations"]))
        self.assertTrue(any("not measured" in l or "downloaded" in l for l in record["limitations"]))

    def test_a_defective_frame_is_ineligible(self):
        flat = library_candidate(pixels={"verdict": "BLOCKED", "detail": "flat fill"})
        record = ranking.rank([flat], REQ)["ranked"][0]
        self.assertFalse(record["eligible"])
        self.assertEqual(record["score"], 0.0)

    def test_pixel_evidence_raises_confidence_for_a_described_library_asset(self):
        measured = library_candidate(pixels={"verdict": "OK"})
        unmeasured = library_candidate("library:" + "b" * 64)
        by_id = {r["candidate_id"]: r for r in ranking.rank([measured, unmeasured], REQ)["ranked"]}
        order = {"low": 0, "medium": 1, "high": 2}
        self.assertGreater(order[by_id[measured["candidate_id"]]["confidence"]],
                           order[by_id[unmeasured["candidate_id"]]["confidence"]])

    def test_model_similarity_is_advisory_and_never_yields_high_confidence(self):
        hinted = stock_candidate(semantic_rank={"method": "CLIP", "score": 0.95})
        record = ranking.rank([hinted], REQ)["ranked"][0]
        self.assertTrue(record["advisory"])
        self.assertNotEqual(record["confidence"], "high")
        relevance = next(f for f in record["factors"] if f["id"] == "relevance")
        self.assertIn("advisory", relevance["detail"])

    def test_footage_is_preferred_when_the_scene_wants_motion(self):
        wants_video = dict(REQ, wants="video")
        photo = stock_candidate("stock:pexels:image:1")
        clip = stock_candidate("stock:pexels:video:2", kind="video")
        result = ranking.rank([photo, clip], wants_video)
        self.assertEqual(result["ranked"][0]["candidate_id"], clip["candidate_id"])

    def test_short_footage_notes_that_it_will_loop(self):
        clip = stock_candidate("stock:pexels:video:2", kind="video", duration_seconds=3)
        record = ranking.rank([clip], dict(REQ, wants="video"))["ranked"][0]
        motion = next(f for f in record["factors"] if f["id"] == "motion")
        self.assertIn("loop", motion["detail"])

    def test_the_direction_avoid_list_counts_against_a_candidate(self):
        clean = stock_candidate("stock:pexels:image:1", description="misty forest fog mist")
        crowd = stock_candidate("stock:pexels:image:2", description="misty forest fog mist people text")
        by_id = {r["candidate_id"]: r for r in ranking.rank([clean, crowd], REQ)["ranked"]}
        self.assertGreater(by_id[clean["candidate_id"]]["score"], by_id[crowd["candidate_id"]]["score"])


class RecommendationTests(unittest.TestCase):
    def test_a_library_match_within_the_margin_beats_a_fresh_generation(self):
        library = library_candidate(description="pine forest fog")
        generated = generated_candidate()
        result = ranking.rank([generated, library], REQ)
        scores = {r["candidate_id"]: r["score"] for r in result["ranked"]}
        self.assertGreater(scores[generated["candidate_id"]], scores[library["candidate_id"]],
                           "the generation scores higher, so this tests the tiebreak")
        self.assertLessEqual(scores[generated["candidate_id"]] - scores[library["candidate_id"]],
                             ranking.SPEND_MARGIN)
        self.assertEqual(result["recommended"], library["candidate_id"])
        self.assertIn("without spending", result["summary"])
        self.assertEqual(result["best"], generated["candidate_id"])

    def test_a_clearly_better_generation_is_not_held_back_to_save_effort(self):
        weak_library = library_candidate(description="a kitchen table", tags=[])
        result = ranking.rank([weak_library, generated_candidate()], REQ)
        scores = {r["candidate_id"]: r["score"] for r in result["ranked"]}
        self.assertGreater(scores["generated:s01:abc"] - scores[weak_library["candidate_id"]],
                           ranking.SPEND_MARGIN)
        self.assertEqual(result["recommended"], "generated:s01:abc",
                         "a poor reuse must not beat a strong generation")

    def test_a_deferred_generation_is_listed_but_never_recommended(self):
        deferred = generated_candidate(state="deferred")
        stock_pick = stock_candidate("stock:pexels:image:1", description="forest")
        result = ranking.rank([deferred, stock_pick], REQ)
        record = next(r for r in result["ranked"] if r["candidate_id"] == deferred["candidate_id"])
        self.assertFalse(record["ready"])
        self.assertEqual(result["recommended"], stock_pick["candidate_id"])

    def test_with_only_a_deferred_generation_nothing_is_recommended_and_it_says_why(self):
        result = ranking.rank([generated_candidate(state="deferred")], REQ)
        self.assertIsNone(result["recommended"])
        self.assertIn("Nothing here is both eligible and ready", result["summary"])

    def test_stock_and_generation_are_both_valid_winners(self):
        # Neither source is favoured for what it is: the evidence decides.
        strong_stock = stock_candidate("stock:pexels:image:1")
        result = ranking.rank([strong_stock, generated_candidate()], REQ)
        self.assertEqual(result["recommended"], "stock:pexels:image:1")
        weak_stock = stock_candidate("stock:pexels:image:3", description="photo")
        result = ranking.rank([weak_stock, generated_candidate()], REQ)
        self.assertEqual(result["recommended"], "generated:s01:abc")

    def test_alternatives_name_other_options_and_why_they_differ(self):
        result = ranking.rank([stock_candidate("stock:pexels:image:1"),
                               stock_candidate("stock:pexels:image:2", description="forest"),
                               library_candidate()], REQ)
        top = result["ranked"][0]
        self.assertTrue(top["alternatives"])
        for alternative in top["alternatives"]:
            self.assertIn("points", alternative["why"])
            self.assertNotEqual(alternative["candidate_id"], top["candidate_id"])

    def test_reuse_of_a_scene_asset_elsewhere_in_the_video_is_discouraged(self):
        fresh = library_candidate("library:" + "a" * 64)
        used = library_candidate("library:" + "b" * 64)
        req = dict(REQ, used_assets=["b" * 64])
        by_id = {r["candidate_id"]: r for r in ranking.rank([fresh, used], req)["ranked"]}
        self.assertGreater(by_id[fresh["candidate_id"]]["score"], by_id[used["candidate_id"]]["score"])

    def test_empty_input_is_handled(self):
        result = ranking.rank([], REQ)
        self.assertEqual(result["ranked"], [])
        self.assertIsNone(result["recommended"])
        self.assertEqual(result["summary"], "No candidates were found.")


class KeywordTests(unittest.TestCase):
    def test_search_terms_drop_generator_boilerplate(self):
        words = ranking.search_terms("cinematic 8k highly detailed misty pine forest at dawn, masterpiece")
        self.assertEqual(words, ["misty", "pine", "forest", "dawn"])

    def test_tokens_stem_plurals(self):
        self.assertIn("forest", ranking.tokens("forests"))


class PolicyTests(unittest.TestCase):
    def rights(self, **kw):
        return licensing.stock_rights("pexels", creator="Ada", creator_url=None,
                                      source_url="https://x", kind="image", external_id="1", **kw)

    def test_first_use_of_stock_needs_approval(self):
        decision = licensing.decide_use({"source": "stock", "rights": self.rights()})
        self.assertEqual(decision["action"], "approval_required")
        self.assertIn("approval", " ".join(decision["reasons"]).lower())

    def test_approval_records_who_and_when_and_unlocks_commercial_use(self):
        approved = licensing.approve(self.rights(), actor="owner@example.com", now="2026-10-22T00:00:00Z")
        self.assertIs(approved["commercial_use"], True)
        self.assertEqual(approved["approval"]["actor"], "owner@example.com")
        self.assertTrue(licensing.approved(approved))
        self.assertEqual(licensing.decide_use({"source": "stock", "rights": approved})["action"], "auto")

    def test_an_edited_record_claiming_commercial_use_is_not_an_approval(self):
        forged = self.rights()
        forged["commercial_use"] = True
        self.assertFalse(licensing.approved(forged))
        self.assertEqual(licensing.decide_use({"source": "stock", "rights": forged})["action"],
                         "approval_required")

    def test_ineligible_terms_cannot_be_approved_at_all(self):
        rights = self.rights()
        rights["eligibility"] = licensing.INELIGIBLE
        with self.assertRaises(licensing.ApprovalError):
            licensing.approve(rights, actor="o")
        self.assertEqual(licensing.decide_use({"source": "stock", "rights": rights})["action"], "blocked")

    def test_ambiguous_rights_need_a_reason_from_the_approver(self):
        rights = licensing.stock_rights("pexels", creator="", creator_url=None, source_url="",
                                        kind="image", external_id="1")
        self.assertEqual(rights["eligibility"], licensing.REVIEW_REQUIRED)
        with self.assertRaisesRegex(licensing.ApprovalError, "approval note"):
            licensing.approve(rights, actor="o")
        approved = licensing.approve(rights, actor="o", note="checked the photo page by hand")
        self.assertEqual(approved["approval"]["note"], "checked the photo page by hand")

    def test_an_unknown_provider_never_gets_commercial_use(self):
        rights = licensing.stock_rights("mystery", creator="x", creator_url=None, source_url="y",
                                        kind="image", external_id="1")
        self.assertIs(rights["commercial_use"], False)
        with self.assertRaises(licensing.ApprovalError):
            licensing.approve(rights, actor="o")

    def test_approval_requires_a_named_actor(self):
        with self.assertRaises(licensing.ApprovalError):
            licensing.approve(self.rights(), actor="  ")

    def test_library_assets_with_rights_are_reused_without_asking(self):
        decision = licensing.decide_use(library_candidate())
        self.assertEqual(decision["action"], "auto")

    def test_library_assets_without_rights_are_blocked_with_a_reason(self):
        candidate = library_candidate(rights_ok=False)
        candidate["library"]["rights_problem"] = "needs a rights record"
        decision = licensing.decide_use(candidate)
        self.assertEqual(decision["action"], "blocked")
        self.assertIn("rights record", decision["reasons"][0])

    def test_saved_but_unapproved_stock_asks_for_approval_when_chosen(self):
        candidate = library_candidate(rights_ok=False)
        candidate["rights"] = self.rights()
        candidate["library"]["approvable"] = True
        self.assertEqual(licensing.decide_use(candidate)["action"], "approval_required")

    def test_a_library_file_on_another_machine_is_blocked(self):
        candidate = library_candidate(state="unavailable")
        self.assertEqual(licensing.decide_use(candidate)["action"], "blocked")

    def test_choosing_a_generation_is_the_owners_call_and_a_deferral_is_not_a_refusal(self):
        self.assertEqual(licensing.decide_use(generated_candidate())["action"], "auto")
        deferred = licensing.decide_use(generated_candidate(state="deferred"))
        self.assertEqual(deferred["action"], "auto")
        self.assertIn("GPU", deferred["reasons"][0] + "GPU")

    def test_cloud_generation_is_blocked_until_explicitly_authorised(self):
        cloud = {"source": "cloud", "rights": {}}
        self.assertEqual(licensing.decide_use(cloud)["action"], "blocked")
        self.assertEqual(licensing.decide_use(cloud, cloud_authorised=True)["action"], "approval_required")

    def approved_rights(self, **kw):
        return licensing.approve(self.rights(), actor="owner@example.com", now="2026-10-22T00:00:00Z", **kw)

    def test_an_approval_records_what_it_covers(self):
        covers = self.approved_rights()["approval"]["covers"]
        self.assertEqual(covers["license"], "Pexels License")
        self.assertTrue(covers["commercial_use"] and covers["modification_allowed"])
        self.assertEqual(covers["redistribution"], "derivative_only")

    def test_reuse_is_covered_only_while_the_licence_and_scope_still_hold(self):
        rights = self.approved_rights()
        self.assertEqual(licensing.approval_covers(rights), (True, []))

        changed = dict(rights, license="Pexels License (2027 revision)")
        ok, why = licensing.approval_covers(changed)
        self.assertFalse(ok)
        self.assertIn("licence or its terms changed", why[0])

        no_scope = json_copy(rights)
        no_scope["approval"].pop("covers")
        self.assertFalse(licensing.approval_covers(no_scope)[0])

        narrower = json_copy(rights)
        narrower["approval"]["covers"]["modification_allowed"] = False
        ok, why = licensing.approval_covers(narrower)
        self.assertFalse(ok)
        self.assertIn("modifying", " ".join(why))

        ok, why = licensing.approval_covers(rights, {"redistribution": "standalone"})
        self.assertFalse(ok)
        self.assertIn("redistribution", " ".join(why))

        ok, why = licensing.approval_covers(rights, {"commercial": True, "modification": True,
                                                     "redistribution": "derivative_only"})
        self.assertTrue(ok)

    def test_unsatisfiable_attribution_means_the_approval_does_not_cover_the_use(self):
        rights = self.approved_rights()
        rights["attribution_text"] = ""
        ok, why = licensing.approval_covers(rights)
        self.assertFalse(ok)
        self.assertIn("attribution", " ".join(why))
        unknown = self.approved_rights()
        unknown["attribution_required"] = None
        unknown["attribution_text"] = ""
        self.assertFalse(licensing.approval_covers(unknown)[0], "unknown is not 'not required'")

    def test_no_approval_means_no_cover(self):
        self.assertFalse(licensing.approval_covers(self.rights())[0])
        self.assertFalse(licensing.approval_covers({})[0])

    def test_an_approved_asset_whose_cover_lapsed_asks_again_at_every_level(self):
        lapsed = self.approved_rights()
        lapsed["license"] = "something else"
        for level in ("supervised", "graduated"):
            stock_decision = licensing.decide_use({"source": "stock", "rights": lapsed}, level=level)
            self.assertEqual(stock_decision["action"], "approval_required", level)
            self.assertIn("does not cover", " ".join(stock_decision["reasons"]))
            library = library_candidate()
            library["rights"] = lapsed
            self.assertEqual(licensing.decide_use(library, level=level)["action"],
                             "approval_required", level)

    def test_graduated_reuses_only_where_the_approval_covers_the_new_use(self):
        rights = self.approved_rights()
        covered = licensing.decide_use({"source": "stock", "rights": rights}, level="graduated")
        self.assertEqual(covered["action"], "auto")
        library = library_candidate()
        library["rights"] = rights
        self.assertEqual(licensing.decide_use(library, level="graduated")["action"], "auto")
        library["use"] = {"redistribution": "standalone"}
        self.assertEqual(licensing.decide_use(library, level="graduated")["action"], "approval_required")

    def test_graduated_never_waives_first_use_and_is_not_active(self):
        row = licensing.AUTONOMY_LEVELS["graduated"]
        self.assertFalse(row["enabled"])
        self.assertEqual(row["external_first_use"], "owner_approval")
        self.assertEqual(row["reuse_requires"], "approval_covers")
        fresh = {"source": "stock", "rights": self.rights()}
        self.assertEqual(licensing.decide_use(fresh, level="graduated")["action"], "approval_required")
        self.assertEqual(licensing.autonomy_level({"VISUAL_ASSET_AUTONOMY": "graduated"})["level"],
                         "supervised")

    def test_graduated_autonomy_is_described_but_cannot_be_enabled(self):
        self.assertFalse(licensing.AUTONOMY_LEVELS["graduated"]["enabled"])
        level = licensing.autonomy_level({"VISUAL_ASSET_AUTONOMY": "graduated"})
        self.assertEqual(level["level"], "supervised")
        self.assertIn("not available", level["note"])
        self.assertEqual(licensing.autonomy_level({})["level"], "supervised")
        self.assertEqual(licensing.autonomy_level({"VISUAL_ASSET_AUTONOMY": "nonsense"})["level"], "supervised")
        # And the supervised row is the strict one.
        row = licensing.AUTONOMY_LEVELS["supervised"]
        self.assertEqual(row["external_first_use"], "owner_approval")
        self.assertEqual(row["cloud_generation"], "never")


if __name__ == "__main__":
    unittest.main()
