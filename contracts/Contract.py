# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
from dataclasses import dataclass
import json
from urllib.parse import urlparse

UserError = gl.vm.UserError


def _addr_str(addr: Address) -> str:
    try:
        return addr.as_hex.lower()
    except Exception:
        return str(addr).lower()


def _extract_origin(url: str) -> tuple:
    u = url.strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        raise UserError("URL must start with http:// or https://")
    try:
        parsed = urlparse(u)
    except Exception:
        raise UserError("Invalid URL format")

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise UserError("Only http and https protocols are supported")

    if parsed.username is not None or parsed.password is not None:
        raise UserError("URL credentials are not allowed")

    hostname = parsed.hostname
    if not hostname:
        raise UserError("URL missing valid hostname")

    hostname = hostname.lower().strip()
    if not hostname or ".." in hostname or hostname.startswith(".") or hostname.endswith("."):
        raise UserError("Ambiguous or invalid hostname")

    port = parsed.port
    if port is None:
        port = 80 if scheme == "http" else 443

    return scheme, hostname, port


def _is_origin_valid(target_url: str, base_url: str) -> bool:
    t_scheme, t_host, t_port = _extract_origin(target_url)
    b_scheme, b_host, b_port = _extract_origin(base_url)

    if t_scheme != b_scheme or t_port != b_port:
        return False

    if t_host == b_host:
        return True

    if t_host.endswith("." + b_host):
        return True

    return False


def _parse_llm_json(text) -> dict:
    if isinstance(text, dict):
        return text
    if hasattr(text, "content"):
        text = text.content
    try:
        cleaned = str(text).strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return json.loads(cleaned.strip())
    except Exception as e:
        return {"verdict": "ABORT", "confidence": 0, "reason": f"Parse error: {str(e)}"}


def _safe_parse(raw) -> dict:
    data = _parse_llm_json(raw)
    if not isinstance(data, dict):
        return None

    verdict = str(data.get("verdict", "")).strip().upper()
    if verdict not in ("INFRINGING_COPY", "FAIR_USE", "UNRELATED", "ABORT"):
        return None

    conf = data.get("confidence", 0)
    if isinstance(conf, float):
        conf = int(conf)
    if not isinstance(conf, int) or not (0 <= conf <= 100):
        return None

    reason = str(data.get("reason", ""))

    if conf < 75 and verdict != "ABORT":
        verdict = "ABORT"
        reason = f"[low_confidence: {conf}%] " + reason

    return {
        "verdict": verdict,
        "confidence": conf,
        "reason": reason[:300],
    }


@allow_storage
@dataclass
class OriginalWork:
    work_id: str
    owner: str
    title: str
    official_source_url: str
    license_terms: str
    total_claims: bigint


@allow_storage
@dataclass
class InfringementClaim:
    claim_id: str
    work_id: str
    infringing_url: str
    specific_allegation: str
    status: str       # PENDING | INFRINGING_CONFIRMED | FAIR_USE_CONFIRMED | UNRELATED_DISMISSED | ESCALATED
    verdict: str      # INFRINGING_COPY | FAIR_USE | UNRELATED | ABORT
    confidence: bigint
    legal_reasoning: str


class Contract(gl.Contract):
    works: TreeMap[str, OriginalWork]
    claims: TreeMap[str, InfringementClaim]
    work_counter: bigint
    total_infringements_recorded: bigint
    compliance_arbiter: str

    def __init__(self):
        self.work_counter = bigint(0)
        self.total_infringements_recorded = bigint(0)
        self.compliance_arbiter = _addr_str(gl.message.sender_address)

    @gl.public.write
    def register_original_work(
        self,
        title: str,
        official_source_url: str,
        license_terms: str,
    ) -> str:
        title = title.strip()
        official_source_url = official_source_url.strip()
        license_terms = license_terms.strip()

        if len(title) < 3:
            raise UserError("Title too short")
        if len(license_terms) < 5:
            raise UserError("License terms too short")

        _extract_origin(official_source_url)

        self.work_counter += bigint(1)
        wid = str(self.work_counter)

        self.works[wid] = OriginalWork(
            work_id=wid,
            owner=_addr_str(gl.message.sender_address),
            title=title,
            official_source_url=official_source_url,
            license_terms=license_terms,
            total_claims=bigint(0),
        )
        return wid

    @gl.public.write
    def file_and_adjudicate_claim(
        self,
        work_id: str,
        infringing_url: str,
        specific_allegation: str,
    ) -> str:
        if work_id not in self.works:
            raise UserError("Registered work not found")
        work = self.works[work_id]

        infringing_url = infringing_url.strip()
        specific_allegation = specific_allegation.strip()

        if len(specific_allegation) < 15:
            raise UserError("Specific allegation too short (min 15 chars)")

        _extract_origin(infringing_url)

        work.total_claims += bigint(1)
        cid = work_id + "_" + str(work.total_claims)

        self.claims[cid] = InfringementClaim(
            claim_id=cid,
            work_id=work_id,
            infringing_url=infringing_url,
            specific_allegation=specific_allegation,
            status="PENDING",
            verdict="",
            confidence=bigint(0),
            legal_reasoning="",
        )
        self.works[work_id] = work

        u_orig = str(work.official_source_url)
        u_infr = str(infringing_url)
        w_title = str(work.title)
        w_lic = str(work.license_terms)
        allegation = str(specific_allegation)

        def leader_fn():
            try:
                res_orig = gl.nondet.web.render(u_orig, mode="text")
                orig_text = res_orig.content if hasattr(res_orig, "content") else str(res_orig)
                if not orig_text or len(orig_text.strip()) < 30:
                    return {"verdict": "ABORT", "confidence": 0, "reason": "Original work URL empty"}
            except Exception as e:
                return {"verdict": "ABORT", "confidence": 0, "reason": f"Original fetch error: {str(e)}"}

            try:
                res_infr = gl.nondet.web.render(u_infr, mode="text")
                infr_text = res_infr.content if hasattr(res_infr, "content") else str(res_infr)
                if not infr_text or len(infr_text.strip()) < 30:
                    return {"verdict": "ABORT", "confidence": 0, "reason": "Suspected infringing URL empty"}
            except Exception as e:
                return {"verdict": "ABORT", "confidence": 0, "reason": f"Infringing fetch error: {str(e)}"}

            prompt = f"""
SYSTEM: You are the Autonomous Decentralized Intellectual Property & Copyright Court.
Evaluate whether the suspected material constitutes an infringing copy of the registered original work.

REGISTERED WORK: {w_title}
LICENSE TERMS: {w_lic}
ALLEGATION DETAILS: {allegation}

ORIGINAL AUTHORITATIVE CONTENT:
{orig_text[:3500]}

SUSPECTED INFRINGING CONTENT:
{infr_text[:3500]}

Rules:
- INFRINGING_COPY (conf >= 75): Extensive plagiarized text, unauthorized distribution, identical proprietary logic, or direct license violation without attribution.
- FAIR_USE (conf >= 75): Transformative commentary, critical analysis, brief quotation with proper credit, or demonstrably independent creation.
- UNRELATED (conf >= 75): Content has no substantial similarity to the original work.
- ABORT: Pages are 404, rate-limited, captcha-blocked, or unreadable.

OUTPUT ONLY STRICT JSON:
{{
  "verdict": "INFRINGING_COPY" | "FAIR_USE" | "UNRELATED" | "ABORT",
  "confidence": 0-100,
  "reason": "max 300 chars technical legal justification"
}}
"""
            try:
                raw1 = gl.nondet.exec_prompt(prompt, response_format="json")
                raw2 = gl.nondet.exec_prompt(prompt, response_format="json")

                p1 = _safe_parse(raw1)
                p2 = _safe_parse(raw2)

                if p1 is None or p2 is None:
                    return {"verdict": "ABORT", "confidence": 0, "reason": "parse_failed"}

                if p1["verdict"] != p2["verdict"]:
                    return {"verdict": "ABORT", "confidence": 0, "reason": "multi_sample_divergence"}

                p1["confidence"] = (p1["confidence"] + p2["confidence"]) // 2
                return p1
            except Exception as e:
                return {"verdict": "ABORT", "confidence": 0, "reason": f"LLM error: {str(e)}"}

        def validator_fn(leader_res) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False

            leader_data = leader_res.calldata if hasattr(leader_res, "calldata") else leader_res
            leader = _safe_parse(leader_data)
            if leader is None:
                return False

            mine = _safe_parse(leader_fn())
            if mine is None:
                return False

            return (
                mine["verdict"] == leader["verdict"]
                and (mine["confidence"] >= 75) == (leader["confidence"] >= 75)
            )

        result_raw = gl.vm.run_nondet(leader_fn, validator_fn)
        result = _safe_parse(result_raw)

        if result is None:
            result = {"verdict": "ABORT", "confidence": 0, "reason": "adjudication_failed"}

        verdict = result["verdict"]
        confidence = result["confidence"]
        reason = result["reason"]

        if confidence < 75 and verdict != "ABORT":
            verdict = "ABORT"

        claim = self.claims[cid]
        claim.verdict = verdict
        claim.confidence = bigint(confidence)
        claim.legal_reasoning = reason

        if verdict == "INFRINGING_COPY":
            claim.status = "INFRINGING_CONFIRMED"
            self.total_infringements_recorded += bigint(1)
        elif verdict == "FAIR_USE":
            claim.status = "FAIR_USE_CONFIRMED"
        elif verdict == "UNRELATED":
            claim.status = "UNRELATED_DISMISSED"
        else:
            claim.status = "ESCALATED"

        self.claims[cid] = claim
        return cid

    @gl.public.write
    def resolve_escalated_claim(
        self,
        claim_id: str,
        manual_verdict: str,
        override_reason: str,
    ) -> None:
        if claim_id not in self.claims:
            raise UserError("Claim not found")
        claim = self.claims[claim_id]

        if claim.status != "ESCALATED":
            raise UserError("Claim is not in ESCALATED state")

        sender = _addr_str(gl.message.sender_address)
        if sender != self.compliance_arbiter:
            raise UserError("Only authorized arbiter can resolve escalated claims")

        v_upper = manual_verdict.strip().upper()
        if v_upper not in ("INFRINGING_COPY", "FAIR_USE", "UNRELATED"):
            raise UserError("Invalid manual verdict choice")

        if v_upper == "INFRINGING_COPY":
            claim.status = "INFRINGING_CONFIRMED"
            self.total_infringements_recorded += bigint(1)
        elif v_upper == "FAIR_USE":
            claim.status = "FAIR_USE_CONFIRMED"
        else:
            claim.status = "UNRELATED_DISMISSED"

        claim.verdict = f"RESOLVED_MANUALLY_{v_upper}"
        claim.legal_reasoning = f"Arbiter override ({sender}): {override_reason[:200]}"
        self.claims[claim_id] = claim

    @gl.public.view
    def is_claim_infringing(self, claim_id: str) -> bool:
        if claim_id not in self.claims:
            return False
        return self.claims[claim_id].status == "INFRINGING_CONFIRMED"

    @gl.public.view
    def get_work(self, work_id: str) -> str:
        if work_id not in self.works:
            raise UserError("Work not found")
        w = self.works[work_id]
        return json.dumps({
            "work_id": w.work_id,
            "owner": w.owner,
            "title": w.title,
            "official_source_url": w.official_source_url,
            "license_terms": w.license_terms,
            "total_claims": str(w.total_claims),
        })

    @gl.public.view
    def get_claim(self, claim_id: str) -> str:
        if claim_id not in self.claims:
            raise UserError("Claim not found")
        c = self.claims[claim_id]
        return json.dumps({
            "claim_id": c.claim_id,
            "work_id": c.work_id,
            "infringing_url": c.infringing_url,
            "specific_allegation": c.specific_allegation,
            "status": c.status,
            "verdict": c.verdict,
            "confidence": str(c.confidence),
            "legal_reasoning": c.legal_reasoning,
        })

    @gl.public.view
    def get_stats(self) -> str:
        return json.dumps({
            "total_registered_works": str(self.work_counter),
            "total_infringements_confirmed": str(self.total_infringements_recorded),
        })
