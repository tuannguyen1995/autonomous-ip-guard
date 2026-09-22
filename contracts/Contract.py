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
        return {
            "verdict": "ABORT",
            "confidence": 0,
            "provenance_evidence": "none",
            "authorization_evidence": "none",
            "reason": f"Parse error: {str(e)}",
        }


def _safe_parse(raw) -> dict:
    data = _parse_llm_json(raw)
    if not isinstance(data, dict):
        return None

    verdict = str(data.get("verdict", "")).strip().upper()
    valid_verdicts = (
        "INFRINGING_COPY",
        "AUTHORIZED_USE",
        "FAIR_USE",
        "UNVERIFIED_PROVENANCE",
        "UNRELATED",
        "ABORT",
    )
    if verdict not in valid_verdicts:
        return None

    conf = data.get("confidence", 0)
    if isinstance(conf, float):
        conf = int(conf)
    if not isinstance(conf, int) or not (0 <= conf <= 100):
        return None

    prov_ev = str(data.get("provenance_evidence", ""))[:200]
    auth_ev = str(data.get("authorization_evidence", ""))[:200]
    reason = str(data.get("reason", ""))

    if conf < 75 and verdict != "ABORT":
        verdict = "ABORT"
        reason = f"[low_confidence: {conf}%] " + reason

    return {
        "verdict": verdict,
        "confidence": conf,
        "provenance_evidence": prov_ev,
        "authorization_evidence": auth_ev,
        "reason": reason[:300],
    }


@allow_storage
@dataclass
class OriginalWork:
    work_id: str
    owner: str
    author_identity: str
    title: str
    official_source_url: str
    license_terms: str
    total_claims: bigint
    verified_license: str
    provenance_status: str


@allow_storage
@dataclass
class InfringementClaim:
    claim_id: str
    work_id: str
    infringing_url: str
    specific_allegation: str
    status: str       # PENDING | INFRINGING_CONFIRMED | AUTHORIZED_CONFIRMED | FAIR_USE_CONFIRMED | PROVENANCE_REJECTED | UNRELATED_DISMISSED | ESCALATED
    verdict: str      # INFRINGING_COPY | AUTHORIZED_USE | FAIR_USE | UNVERIFIED_PROVENANCE | UNRELATED | ABORT
    confidence: bigint
    legal_reasoning: str
    provenance_evidence: str
    authorization_evidence: str


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
        author_identity: str = "",
    ) -> str:
        title = title.strip()
        official_source_url = official_source_url.strip()
        license_terms = license_terms.strip()
        author_identity = author_identity.strip()

        sender = _addr_str(gl.message.sender_address)
        if not author_identity:
            author_identity = sender

        if len(title) < 3:
            raise UserError("Title too short")
        if len(license_terms) < 5:
            raise UserError("License terms too short")
        if len(author_identity) < 3:
            raise UserError("Author identity too short")

        _extract_origin(official_source_url)

        self.work_counter += bigint(1)
        wid = str(self.work_counter)

        self.works[wid] = OriginalWork(
            work_id=wid,
            owner=sender,
            author_identity=author_identity,
            title=title,
            official_source_url=official_source_url,
            license_terms=license_terms,
            total_claims=bigint(0),
            verified_license="PENDING_VERIFICATION",
            provenance_status="UNVERIFIED",
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
            provenance_evidence="",
            authorization_evidence="",
        )
        self.works[work_id] = work

        u_orig = str(work.official_source_url)
        u_infr = str(infringing_url)
        w_title = str(work.title)
        w_lic = str(work.license_terms)
        w_author = str(work.author_identity)
        w_owner = str(work.owner)
        allegation = str(specific_allegation)

        def leader_fn():
            try:
                res_orig = gl.nondet.web.render(u_orig, mode="text")
                orig_text = res_orig.content if hasattr(res_orig, "content") else str(res_orig)
                if not orig_text or len(orig_text.strip()) < 30:
                    return {
                        "verdict": "ABORT",
                        "confidence": 0,
                        "provenance_evidence": "none",
                        "authorization_evidence": "none",
                        "reason": "Original work URL empty or unreadable",
                    }
            except Exception as e:
                return {
                    "verdict": "ABORT",
                    "confidence": 0,
                    "provenance_evidence": "none",
                    "authorization_evidence": "none",
                    "reason": f"Original fetch error: {str(e)}",
                }

            try:
                res_infr = gl.nondet.web.render(u_infr, mode="text")
                infr_text = res_infr.content if hasattr(res_infr, "content") else str(res_infr)
                if not infr_text or len(infr_text.strip()) < 30:
                    return {
                        "verdict": "ABORT",
                        "confidence": 0,
                        "provenance_evidence": "none",
                        "authorization_evidence": "none",
                        "reason": "Suspected infringing URL empty or unreadable",
                    }
            except Exception as e:
                return {
                    "verdict": "ABORT",
                    "confidence": 0,
                    "provenance_evidence": "none",
                    "authorization_evidence": "none",
                    "reason": f"Infringing fetch error: {str(e)}",
                }

            prompt = f"""
SYSTEM: You are the Autonomous Decentralized Intellectual Property & Copyright Court on GenLayer.
You must perform a 3-part authoritative analysis before deciding copyright infringement:

1. PROVENANCE & OWNERSHIP AUDIT:
- Expected Author / Identity: {w_author}
- Registered Owner Address: {w_owner}
- Registered Title: {w_title}
- Claimed License: {w_lic}
Does the ORIGINAL AUTHORITATIVE CONTENT positively identify or corroborate this author/owner (e.g. byline, copyright header, author bio, handle, organization, or wallet address)?
Does the declared license align with the license terms present on the authoritative page?
If the original source does NOT corroborate authorship, or if the declared license directly contradicts the page, output UNVERIFIED_PROVENANCE.

2. AUTHORIZATION & ATTRIBUTION AUDIT:
Examine the SUSPECTED INFRINGING CONTENT:
Does it contain an explicit authorization notice, sub-license grant, or compliant attribution (e.g. author credit, license link) that satisfies the original license terms?
If copying is authorized or attribution compliant under the applicable license, output AUTHORIZED_USE.

3. INFRINGEMENT & SIMILARITY AUDIT:
Allegation: {allegation}
Evaluate whether unauthorized copying exceeding fair use has occurred.

ORIGINAL AUTHORITATIVE CONTENT:
{orig_text[:3500]}

SUSPECTED INFRINGING CONTENT:
{infr_text[:3500]}

Rules:
- INFRINGING_COPY (conf >= 75): Provenance verified, declared license verified, but suspected material is an unauthorized reproduction lacking permission or required attribution.
- AUTHORIZED_USE (conf >= 75): Copying is authorized, explicitly permitted by author, or complies fully with attribution terms of the original license.
- FAIR_USE (conf >= 75): Transformative commentary, critical analysis, brief quotation with proper credit, or demonstrably independent creation.
- UNVERIFIED_PROVENANCE (conf >= 75): Authoritative source fails to substantiate registrant ownership/authorship, or declared license contradicts page evidence.
- UNRELATED (conf >= 75): Content has no substantial similarity to original work.
- ABORT: Pages are 404, rate-limited, captcha-blocked, or unreadable.

OUTPUT ONLY STRICT JSON:
{{
  "verdict": "INFRINGING_COPY" | "AUTHORIZED_USE" | "FAIR_USE" | "UNVERIFIED_PROVENANCE" | "UNRELATED" | "ABORT",
  "confidence": 0-100,
  "provenance_evidence": "max 200 chars describing author and license verification",
  "authorization_evidence": "max 200 chars describing presence or absence of permission/attribution",
  "reason": "max 300 chars technical legal justification"
}}
"""
            try:
                raw1 = gl.nondet.exec_prompt(prompt, response_format="json")
                raw2 = gl.nondet.exec_prompt(prompt, response_format="json")

                p1 = _safe_parse(raw1)
                p2 = _safe_parse(raw2)

                if p1 is None or p2 is None:
                    return {
                        "verdict": "ABORT",
                        "confidence": 0,
                        "provenance_evidence": "parse_failure",
                        "authorization_evidence": "parse_failure",
                        "reason": "parse_failed",
                    }

                if p1["verdict"] != p2["verdict"]:
                    return {
                        "verdict": "ABORT",
                        "confidence": 0,
                        "provenance_evidence": "divergence",
                        "authorization_evidence": "divergence",
                        "reason": "multi_sample_divergence",
                    }

                p1["confidence"] = (p1["confidence"] + p2["confidence"]) // 2
                return p1
            except Exception as e:
                return {
                    "verdict": "ABORT",
                    "confidence": 0,
                    "provenance_evidence": "llm_error",
                    "authorization_evidence": "llm_error",
                    "reason": f"LLM error: {str(e)}",
                }

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
            result = {
                "verdict": "ABORT",
                "confidence": 0,
                "provenance_evidence": "adjudication_failed",
                "authorization_evidence": "adjudication_failed",
                "reason": "adjudication_failed",
            }

        verdict = result["verdict"]
        confidence = result["confidence"]
        reason = result["reason"]
        prov_ev = result["provenance_evidence"]
        auth_ev = result["authorization_evidence"]

        if confidence < 75 and verdict != "ABORT":
            verdict = "ABORT"

        claim = self.claims[cid]
        claim.verdict = verdict
        claim.confidence = bigint(confidence)
        claim.legal_reasoning = reason
        claim.provenance_evidence = prov_ev
        claim.authorization_evidence = auth_ev

        if verdict == "INFRINGING_COPY":
            claim.status = "INFRINGING_CONFIRMED"
            work.provenance_status = "VERIFIED_AUTHORITATIVE"
            self.total_infringements_recorded += bigint(1)
        elif verdict == "AUTHORIZED_USE":
            claim.status = "AUTHORIZED_CONFIRMED"
            work.provenance_status = "VERIFIED_AUTHORITATIVE"
        elif verdict == "FAIR_USE":
            claim.status = "FAIR_USE_CONFIRMED"
            work.provenance_status = "VERIFIED_AUTHORITATIVE"
        elif verdict == "UNVERIFIED_PROVENANCE":
            claim.status = "PROVENANCE_REJECTED"
            work.provenance_status = "DISPUTED"
        elif verdict == "UNRELATED":
            claim.status = "UNRELATED_DISMISSED"
        else:
            claim.status = "ESCALATED"

        self.claims[cid] = claim
        self.works[work_id] = work
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
        allowed = (
            "INFRINGING_COPY",
            "AUTHORIZED_USE",
            "FAIR_USE",
            "UNVERIFIED_PROVENANCE",
            "UNRELATED",
        )
        if v_upper not in allowed:
            raise UserError("Invalid manual verdict choice")

        if v_upper == "INFRINGING_COPY":
            claim.status = "INFRINGING_CONFIRMED"
            self.total_infringements_recorded += bigint(1)
        elif v_upper == "AUTHORIZED_USE":
            claim.status = "AUTHORIZED_CONFIRMED"
        elif v_upper == "FAIR_USE":
            claim.status = "FAIR_USE_CONFIRMED"
        elif v_upper == "UNVERIFIED_PROVENANCE":
            claim.status = "PROVENANCE_REJECTED"
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
            "author_identity": w.author_identity,
            "title": w.title,
            "official_source_url": w.official_source_url,
            "license_terms": w.license_terms,
            "total_claims": str(w.total_claims),
            "verified_license": w.verified_license,
            "provenance_status": w.provenance_status,
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
            "provenance_evidence": c.provenance_evidence,
            "authorization_evidence": c.authorization_evidence,
        })

    @gl.public.view
    def get_stats(self) -> str:
        return json.dumps({
            "total_registered_works": str(self.work_counter),
            "total_infringements_confirmed": str(self.total_infringements_recorded),
        })
