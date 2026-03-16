"""
Gap Detection Service for Ada AI Protocol Generation Platform

Analyzes extracted protocol JSON to detect:
- Missing critical and recommended fields
- Low-confidence values
- Cross-reference inconsistencies
- Regulatory compliance gaps (GLP-specific)

Used by the protocol extraction pipeline to guide users through data completeness
and flag issues before protocol finalization.
"""

from typing import Dict, List, Any, Tuple, Callable, Optional
from dataclasses import dataclass, asdict
import re


# ============================================================================
# CRITICAL FIELDS - Required for GLP Compliance
# ============================================================================

CRITICAL_FIELDS = {
    "BasicDetails.StudyNo": "Study Number - Unique identifier for the entire study",
    "BasicDetails.StudyTitle": "Study Title - Full descriptive title of the study",
    "BasicDetails.StudyDirector": "Study Director - Primary responsible person",
    "BasicDetails.StudySponsor": "Study Sponsor - Organization funding the study",
    "BasicDetails.CRO": "Contract Research Organization - Facility conducting study",

    "StudyInfo.ClinicalPhase": "Clinical Phase - Phase I, II, III, IV or preclinical",
    "StudyInfo.StudyType": "Study Type - e.g., Toxicology, Pharmacokinetics, Safety",
    "StudyInfo.Indication": "Therapeutic Indication - Disease/condition being studied",
    "StudyInfo.StudyStartDate": "Study Start Date - Official study initiation date",
    "StudyInfo.StudyEndDate": "Study End Date - Projected or actual completion date",

    "ExperimentalDesign.Species": "Animal Species - Primary test species (e.g., rat, dog, monkey)",
    "ExperimentalDesign.Strain": "Animal Strain - Specific strain/line designation",
    "ExperimentalDesign.NumberOfAnimals": "Total Animal Count - Total animals in all groups",
    "ExperimentalDesign.GroupCount": "Number of Groups - Total experimental/control groups",
    "ExperimentalDesign.DurationInDays": "Study Duration - Total length in days",
    "ExperimentalDesign.RouteOfAdministration": "Route of Administration - e.g., oral, IV, dermal, inhalation",

    "TestMaterial.MaterialID": "Test Material ID - Unique identifier for drug/chemical",
    "TestMaterial.MaterialName": "Test Material Name - Commercial or chemical name",
    "TestMaterial.Purity": "Purity Specification - % purity of test material",
    "TestMaterial.Batch": "Batch/Lot Number - Manufacturing batch identifier",
    "TestMaterial.ManufacturedDate": "Manufacturing Date - Date material was produced",

    "ExperimentalObservationsandProcedures.RouteOfAdmin": "Route of Administration (Procedures) - Dosing route used",
    "ExperimentalObservationsandProcedures.DoseFrequency": "Dose Frequency - How often dosing occurs",
    "ExperimentalObservationsandProcedures.DoseGroup": "Dose Groups - All dose levels tested",

    "RegulatoryCompliance.GLPCompliant": "GLP Compliance Flag - Whether study is GLP-compliant",
    "RegulatoryCompliance.TestGuidelineUsed": "Test Guideline - e.g., OECD 414, EPA OPPTS, ICH",
    "RegulatoryCompliance.Jurisdiction": "Regulatory Jurisdiction - Region/country of oversight",

    "DataAnalysis.StatisticalMethod": "Statistical Analysis Method - Method for data analysis",
    "DataAnalysis.SignificanceLevel": "Significance Level - e.g., p<0.05",

    "Reporting.ReportDate": "Report Date - Date report completed",
    "Reporting.QualityAssuranceReview": "QA Review Completion - Evidence of QA review",
}


# ============================================================================
# RECOMMENDED FIELDS - Important but Non-Blocking
# ============================================================================

RECOMMENDED_FIELDS = {
    "BasicDetails.Protocol": "Protocol Version - Document version number",
    "BasicDetails.Confidentiality": "Confidentiality Statement - GLP requirement",

    "StudyInfo.StudyRationale": "Study Rationale - Scientific justification",
    "StudyInfo.ReferenceSubstances": "Reference Substances - Positive/negative controls",
    "StudyInfo.BackgroundHazards": "Background Hazards - Known risks to animals",

    "Personnel.QualityAssuranceManager": "QA Manager Name - Responsible for oversight",
    "Personnel.PrincipalInvestigator": "Principal Investigator - Lead scientist",
    "Personnel.ToxicologistOnStaff": "Toxicologist - Subject matter expert",

    "TestSystem.AnimalSuppliersandAcclimation": "Animal Supplier - Source and acclimation details",
    "TestSystem.HousingandEnvironment": "Housing Details - Temperature, humidity, lighting",
    "TestSystem.Food": "Food Type - Brand and specifications",

    "Husbandry.FoodProvision": "Food Provision Details - Unlimited vs. restricted",
    "Husbandry.WaterSource": "Water Source - Type and treatment",
    "Husbandry.CageSize": "Cage Dimensions - Space per animal",

    "ClinicalPathology.BloodCollectionMethod": "Blood Collection Method - Cardiac puncture vs. other",
    "ClinicalPathology.BloodParameters": "Hematology Parameters - CBC and coagulation panel",
    "ClinicalPathology.ChemistryParameters": "Chemistry Panel - Organ function markers",

    "TerminalProceduresandPathology.NecropysySchedule": "Necropsy Schedule - When necropsies occur",
    "TerminalProceduresandPathology.OrganWeights": "Organ Weights - Which organs weighed",
    "TerminalProceduresandPathology.HistopathologyScope": "Histology Scope - Which tissues examined",

    "ArchivalStorage.ArchivalPeriod": "Archival Period - Years samples retained",
    "ArchivalStorage.StorageConditions": "Storage Conditions - Temperature/humidity",
    "ArchivalStorage.BackupProtocols": "Backup Protocols - Redundancy measures",

    "Approvals.IACUCApproval": "IACUC Approval - Institutional Animal Care approval",
    "Approvals.EthicsApprovalDate": "Ethics Approval Date - When approval granted",
    "Approvals.RegulatoryApprovals": "Regulatory Approvals - Any pre-study approvals",
}


# ============================================================================
# CROSS-REFERENCE RULES
# ============================================================================

def _check_group_consistency(extracted_json: Dict) -> List[str]:
    """Verify that group counts and references are consistent."""
    issues = []
    try:
        exp_design = extracted_json.get("ExperimentalDesign", {})
        exp_obs = extracted_json.get("ExperimentalObservationsandProcedures", {})

        group_count = exp_design.get("GroupCount")
        dose_groups = exp_obs.get("DoseGroup", [])

        if group_count and dose_groups:
            if isinstance(dose_groups, list):
                if len(dose_groups) != int(group_count):
                    issues.append(
                        f"Group count mismatch: ExperimentalDesign.GroupCount={group_count} "
                        f"but ExperimentalObservationsandProcedures.DoseGroup has {len(dose_groups)} groups"
                    )
    except Exception:
        pass

    return issues


def _check_species_consistency(extracted_json: Dict) -> List[str]:
    """Verify species is consistent across sections."""
    issues = []
    try:
        exp_design_species = extracted_json.get("ExperimentalDesign", {}).get("Species")
        test_system_species = extracted_json.get("TestSystem", {}).get("Species")

        if exp_design_species and test_system_species:
            if exp_design_species.lower() != test_system_species.lower():
                issues.append(
                    f"Species inconsistency: ExperimentalDesign.Species={exp_design_species} "
                    f"but TestSystem.Species={test_system_species}"
                )
    except Exception:
        pass

    return issues


def _check_route_of_admin_consistency(extracted_json: Dict) -> List[str]:
    """Verify route of administration is consistent."""
    issues = []
    try:
        exp_design_route = extracted_json.get("ExperimentalDesign", {}).get("RouteOfAdministration")
        exp_obs_route = extracted_json.get("ExperimentalObservationsandProcedures", {}).get("RouteOfAdmin")

        if exp_design_route and exp_obs_route:
            # Normalize for comparison
            design_route = str(exp_design_route).lower().strip()
            obs_route = str(exp_obs_route).lower().strip()

            if design_route and obs_route and design_route != obs_route:
                issues.append(
                    f"Route of administration inconsistency: ExperimentalDesign.RouteOfAdministration={exp_design_route} "
                    f"but ExperimentalObservationsandProcedures.RouteOfAdmin={exp_obs_route}"
                )
    except Exception:
        pass

    return issues


def _check_duration_alignment(extracted_json: Dict) -> List[str]:
    """Verify study duration aligns with observation schedule."""
    issues = []
    try:
        duration = extracted_json.get("ExperimentalDesign", {}).get("DurationInDays")
        obs_schedule = extracted_json.get("ExperimentalObservationsandProcedures", {}).get("ObservationSchedule")

        if duration and obs_schedule:
            # Check if latest observation falls within study duration
            if isinstance(obs_schedule, list) and obs_schedule:
                max_obs_day = max([
                    int(str(day).split()[0]) for day in obs_schedule
                    if re.search(r'\d+', str(day))
                ], default=0)

                try:
                    duration_int = int(duration)
                    if max_obs_day > duration_int:
                        issues.append(
                            f"Duration alignment issue: Last observation at day {max_obs_day} "
                            f"exceeds study duration of {duration_int} days"
                        )
                except ValueError:
                    pass
    except Exception:
        pass

    return issues


def _check_dosage_consistency(extracted_json: Dict) -> List[str]:
    """Verify dosage information is complete for all groups."""
    issues = []
    try:
        dose_groups = extracted_json.get("ExperimentalObservationsandProcedures", {}).get("DoseGroup", [])
        dose_levels = extracted_json.get("ExperimentalObservationsandProcedures", {}).get("DoseLevel", [])

        if isinstance(dose_groups, list) and dose_groups:
            if not dose_levels or len(dose_levels) < len(dose_groups):
                issues.append(
                    f"Dosage completeness: {len(dose_groups)} dose groups defined "
                    f"but only {len(dose_levels) if dose_levels else 0} dose levels specified"
                )
    except Exception:
        pass

    return issues


def _check_material_batch_info(extracted_json: Dict) -> List[str]:
    """Verify test material batch and manufacturing info is present."""
    issues = []
    try:
        test_material = extracted_json.get("TestMaterial", {})
        batch = test_material.get("Batch")
        mfg_date = test_material.get("ManufacturedDate")

        if not batch:
            issues.append("Test material batch/lot number is missing")
        if not mfg_date:
            issues.append("Test material manufacturing date is missing")
    except Exception:
        pass

    return issues


CROSS_REFERENCE_RULES = [
    {
        "name": "Group Count Consistency",
        "description": "Verify that group count in ExperimentalDesign matches dose groups in procedures",
        "check": _check_group_consistency
    },
    {
        "name": "Species Consistency",
        "description": "Verify species is consistent between ExperimentalDesign and TestSystem",
        "check": _check_species_consistency
    },
    {
        "name": "Route of Administration Consistency",
        "description": "Verify route of administration is consistent across sections",
        "check": _check_route_of_admin_consistency
    },
    {
        "name": "Study Duration Alignment",
        "description": "Verify observation schedule falls within study duration",
        "check": _check_duration_alignment
    },
    {
        "name": "Dosage Information Completeness",
        "description": "Verify dosage levels are defined for all dose groups",
        "check": _check_dosage_consistency
    },
    {
        "name": "Test Material Batch Information",
        "description": "Verify test material batch and manufacturing details are documented",
        "check": _check_material_batch_info
    }
]


# ============================================================================
# GLP REQUIREMENTS
# ============================================================================

GLP_REQUIREMENTS = [
    {
        "requirement": "Study Director Named",
        "field": "BasicDetails.StudyDirector",
        "description": "GLP requires a named Study Director with responsibility for conduct"
    },
    {
        "requirement": "Quality Assurance Review",
        "field": "Reporting.QualityAssuranceReview",
        "description": "GLP requires QA inspection and report sign-off"
    },
    {
        "requirement": "Test Guideline Referenced",
        "field": "RegulatoryCompliance.TestGuidelineUsed",
        "description": "GLP studies must reference applicable test guideline (OECD, EPA, ICH)"
    },
    {
        "requirement": "Regulatory Jurisdiction",
        "field": "RegulatoryCompliance.Jurisdiction",
        "description": "GLP requirements vary by jurisdiction; must be specified"
    },
    {
        "requirement": "Archive Retention",
        "field": "ArchivalStorage.ArchivalPeriod",
        "description": "GLP requires archival of study records for specified retention period"
    },
    {
        "requirement": "Animal Source Documented",
        "field": "TestSystem.AnimalSuppliersandAcclimation",
        "description": "GLP requires documentation of animal source and acclimation period"
    },
    {
        "requirement": "Environmental Controls",
        "field": "TestSystem.HousingandEnvironment",
        "description": "GLP requires documentation of temperature, humidity, and lighting controls"
    },
    {
        "requirement": "Confidentiality Statement",
        "field": "BasicDetails.Confidentiality",
        "description": "GLP documents must include confidentiality statement"
    },
]


# ============================================================================
# GAP DETECTION SERVICE
# ============================================================================

@dataclass
class FieldAnalysis:
    """Analysis of a single protocol field."""
    path: str
    status: str  # "extracted", "missing_critical", "missing_optional", "low_confidence"
    value: Any = None
    description: str = ""
    suggestion: Optional[str] = None
    question: Optional[str] = None
    criticality: str = "optional"


@dataclass
class CrossReferenceIssue:
    """Issue found during cross-reference validation."""
    rule: str
    issue: str
    severity: str  # "error", "warning"


@dataclass
class RegulatoryIssue:
    """GLP compliance issue."""
    requirement: str
    field: str
    status: str  # "met", "missing", "incomplete"


class GapDetectionService:
    """
    Service for analyzing protocol extraction completeness and quality.

    Performs three passes:
    1. Field extraction - checks for presence and confidence of critical/recommended fields
    2. Cross-reference validation - checks consistency across sections
    3. Regulatory compliance - checks GLP-specific requirements
    """

    def analyze(self, extracted_json: Dict, is_glp: bool = True) -> Dict:
        """
        Run complete gap analysis on extracted protocol JSON.

        Args:
            extracted_json: Nested dict with protocol sections and fields
            is_glp: Whether to apply GLP-specific rules (default True)

        Returns:
            Dict with:
            - summary: counts of fields extracted, missing, low-confidence, etc.
            - fields: list of FieldAnalysis dicts for all critical/recommended fields
            - cross_reference_issues: list of CrossReferenceIssue dicts
            - regulatory_issues: list of RegulatoryIssue dicts (if is_glp)
            - completeness_score: 0.0-1.0 indicating overall quality
        """

        # Flatten JSON for easier lookups
        flat_json = self._flatten_json(extracted_json)

        # Detect actual GLP status if not explicitly provided
        actual_is_glp = self._detect_glp_status(extracted_json)
        effective_is_glp = is_glp and actual_is_glp

        # Pass 1: Analyze fields
        field_analyses = self._analyze_fields(flat_json, effective_is_glp)

        # Pass 2: Cross-reference checks
        cross_ref_issues = self._check_cross_references(extracted_json)

        # Pass 3: Regulatory compliance
        regulatory_issues = []
        if effective_is_glp:
            regulatory_issues = self._check_glp_compliance(flat_json)

        # Calculate summary
        summary = self._calculate_summary(field_analyses, cross_ref_issues, regulatory_issues)
        completeness_score = self._calculate_completeness_score(
            field_analyses, cross_ref_issues, regulatory_issues
        )

        return {
            "summary": summary,
            "fields": [asdict(fa) for fa in field_analyses],
            "cross_reference_issues": [asdict(cri) for cri in cross_ref_issues],
            "regulatory_issues": [asdict(ri) for ri in regulatory_issues],
            "completeness_score": completeness_score
        }

    def generate_questions(self, gap_analysis: Dict) -> List[Dict]:
        """
        Generate Q&A wizard questions from gap analysis.

        Each question helps the user fill in missing or low-confidence data.

        Returns:
            List of question dicts with keys:
            - field_path: dot-notation field path
            - question_text: human-readable question
            - why_it_matters: explanation of importance
            - suggestion: suggested value (if available)
            - input_type: "text", "select", "multiselect", "number", "date"
            - options: list of valid options (for select types)
            - criticality: "critical", "recommended", "optional"
        """
        questions = []

        # Get fields that need attention
        fields = gap_analysis.get("fields", [])
        for field in fields:
            if field["status"] in ("missing_critical", "missing_optional", "low_confidence"):
                path = field["path"]

                # Create base question
                question = {
                    "field_path": path,
                    "question_text": self._generate_question_text(path, field),
                    "why_it_matters": field.get("description", ""),
                    "suggestion": field.get("suggestion"),
                    "input_type": self._infer_input_type(path),
                    "options": self._get_field_options(path),
                    "criticality": "critical" if field["status"] == "missing_critical" else
                                   "recommended" if field["status"] == "missing_optional" else
                                   "optional"
                }
                questions.append(question)

        # Add cross-reference clarification questions
        cross_refs = gap_analysis.get("cross_reference_issues", [])
        for issue in cross_refs:
            if issue["severity"] == "error":
                questions.append({
                    "field_path": None,  # Multi-field issue
                    "question_text": f"Please clarify: {issue['issue']}",
                    "why_it_matters": f"Consistency issue detected in '{issue['rule']}'",
                    "suggestion": None,
                    "input_type": "text",
                    "options": None,
                    "criticality": "critical"
                })

        return questions

    def _flatten_json(self, data: Dict, prefix: str = "") -> Dict:
        """
        Flatten nested dict to dot-notation keys.

        Example:
            {"A": {"B": {"C": 1}}} -> {"A.B.C": 1}
        """
        flat = {}

        for key, value in data.items():
            new_key = f"{prefix}.{key}" if prefix else key

            if isinstance(value, dict):
                flat.update(self._flatten_json(value, new_key))
            else:
                flat[new_key] = value

        return flat

    def _check_field_quality(self, value: Any) -> str:
        """
        Assess the quality/confidence of a field value.

        Returns:
            "extracted" - meaningful value present
            "low_confidence" - value present but potentially incomplete/uncertain
            "empty" - no meaningful value
        """

        # Treat None, empty strings, empty collections as empty
        if value is None or value == "" or value == []:
            return "empty"

        # Check for low-confidence markers
        value_str = str(value).lower()
        low_confidence_markers = [
            "unknown", "unclear", "tbd", "to be determined",
            "n/a", "not specified", "?", "[redacted]", "pending"
        ]

        if any(marker in value_str for marker in low_confidence_markers):
            return "low_confidence"

        # For strings, require minimum length
        if isinstance(value, str):
            if len(value.strip()) < 2:
                return "empty"

        return "extracted"

    def _detect_glp_status(self, extracted_json: Dict) -> bool:
        """
        Check if the protocol is GLP-compliant based on RegulatoryCompliance section.

        Returns:
            True if GLP compliance is indicated, False otherwise
        """
        try:
            glp_flag = extracted_json.get("RegulatoryCompliance", {}).get("GLPCompliant")
            if glp_flag:
                glp_str = str(glp_flag).lower()
                return glp_str in ("true", "yes", "1", "compliant", "glp")
        except Exception:
            pass

        return False

    def _analyze_fields(self, flat_json: Dict, is_glp: bool) -> List[FieldAnalysis]:
        """Analyze all critical and recommended fields."""
        analyses = []

        # Analyze critical fields
        for field_path, description in CRITICAL_FIELDS.items():
            value = flat_json.get(field_path)
            quality = self._check_field_quality(value)

            if quality == "empty":
                status = "missing_critical"
            elif quality == "low_confidence":
                status = "low_confidence"
            else:
                status = "extracted"

            analyses.append(FieldAnalysis(
                path=field_path,
                status=status,
                value=value,
                description=description,
                suggestion=self._suggest_field_value(field_path, value),
                criticality="critical"
            ))

        # Analyze recommended fields
        for field_path, description in RECOMMENDED_FIELDS.items():
            value = flat_json.get(field_path)
            quality = self._check_field_quality(value)

            if quality == "empty":
                status = "missing_optional"
            elif quality == "low_confidence":
                status = "low_confidence"
            else:
                status = "extracted"

            analyses.append(FieldAnalysis(
                path=field_path,
                status=status,
                value=value,
                description=description,
                suggestion=self._suggest_field_value(field_path, value),
                criticality="recommended"
            ))

        return analyses

    def _check_cross_references(self, extracted_json: Dict) -> List[CrossReferenceIssue]:
        """Run all cross-reference validation rules."""
        issues = []

        for rule in CROSS_REFERENCE_RULES:
            try:
                issues_found = rule["check"](extracted_json)
                for issue_text in issues_found:
                    issues.append(CrossReferenceIssue(
                        rule=rule["name"],
                        issue=issue_text,
                        severity="error"  # Cross-reference issues are errors
                    ))
            except Exception:
                # Don't fail on individual rule exceptions
                pass

        return issues

    def _check_glp_compliance(self, flat_json: Dict) -> List[RegulatoryIssue]:
        """Check GLP-specific requirements."""
        issues = []

        for req in GLP_REQUIREMENTS:
            field_path = req["field"]
            value = flat_json.get(field_path)
            quality = self._check_field_quality(value)

            if quality == "empty":
                status = "missing"
            elif quality == "low_confidence":
                status = "incomplete"
            else:
                status = "met"

            issues.append(RegulatoryIssue(
                requirement=req["requirement"],
                field=field_path,
                status=status
            ))

        return issues

    def _calculate_summary(
        self,
        field_analyses: List[FieldAnalysis],
        cross_refs: List[CrossReferenceIssue],
        regulatory_issues: List[RegulatoryIssue]
    ) -> Dict:
        """Generate summary statistics."""

        total_fields = len(field_analyses)
        extracted = sum(1 for fa in field_analyses if fa.status == "extracted")
        missing_critical = sum(1 for fa in field_analyses if fa.status == "missing_critical")
        missing_optional = sum(1 for fa in field_analyses if fa.status == "missing_optional")
        low_confidence = sum(1 for fa in field_analyses if fa.status == "low_confidence")

        regulatory_met = sum(1 for ri in regulatory_issues if ri.status == "met")
        regulatory_missing = sum(1 for ri in regulatory_issues if ri.status == "missing")
        regulatory_incomplete = sum(1 for ri in regulatory_issues if ri.status == "incomplete")

        return {
            "total_fields": total_fields,
            "extracted": extracted,
            "missing_critical": missing_critical,
            "missing_optional": missing_optional,
            "low_confidence": low_confidence,
            "cross_ref_issues": len(cross_refs),
            "regulatory_issues_total": len(regulatory_issues),
            "regulatory_met": regulatory_met,
            "regulatory_missing": regulatory_missing,
            "regulatory_incomplete": regulatory_incomplete
        }

    def _calculate_completeness_score(
        self,
        field_analyses: List[FieldAnalysis],
        cross_refs: List[CrossReferenceIssue],
        regulatory_issues: List[RegulatoryIssue]
    ) -> float:
        """
        Calculate overall protocol completeness score (0.0-1.0).

        Scoring:
        - Critical field extracted: +0.5 / num_critical
        - Recommended field extracted: +0.3 / num_recommended
        - Cross-ref error: -0.05 each
        - GLP issue: -0.05 each
        """

        score = 0.0

        # Count critical and recommended
        critical_fields = [fa for fa in field_analyses if fa.criticality == "critical"]
        recommended_fields = [fa for fa in field_analyses if fa.criticality == "recommended"]

        # Critical fields contribute 0.5 total
        if critical_fields:
            critical_extracted = sum(1 for fa in critical_fields if fa.status == "extracted")
            score += (critical_extracted / len(critical_fields)) * 0.5

        # Recommended fields contribute 0.3 total
        if recommended_fields:
            recommended_extracted = sum(1 for fa in recommended_fields if fa.status == "extracted")
            score += (recommended_extracted / len(recommended_fields)) * 0.3

        # Low confidence reduces critical score
        low_confidence_critical = sum(1 for fa in critical_fields if fa.status == "low_confidence")
        score -= (low_confidence_critical / len(critical_fields)) * 0.1 if critical_fields else 0

        # Add base for passing all cross-refs and regulatory
        base_bonus = 0.2
        cross_ref_penalty = len(cross_refs) * 0.03
        regulatory_penalty = sum(1 for ri in regulatory_issues if ri.status != "met") * 0.02

        score += base_bonus - cross_ref_penalty - regulatory_penalty

        # Clamp to 0.0-1.0
        return max(0.0, min(1.0, score))

    def _generate_question_text(self, field_path: str, field_analysis: Dict) -> str:
        """Generate human-readable question for a field."""

        description = field_analysis.get("description", field_path)
        status = field_analysis.get("status")

        if status == "missing_critical":
            return f"What is the {description.split(' - ')[0] if ' - ' in description else description}?"
        elif status == "missing_optional":
            return f"Can you provide the {description.split(' - ')[0].lower() if ' - ' in description else description.lower()}?"
        else:  # low_confidence
            value = field_analysis.get("value")
            return f"Please confirm or update the {description.split(' - ')[0].lower() if ' - ' in description else description.lower()}: {value}"

    def _infer_input_type(self, field_path: str) -> str:
        """Infer appropriate input type for a field."""

        field_lower = field_path.lower()

        # Date fields
        if "date" in field_lower:
            return "date"

        # Number fields
        if any(x in field_lower for x in ["count", "number", "duration", "days", "purity", "level"]):
            return "number"

        # Select fields with known options
        select_fields = {
            "species": ["Rat", "Mouse", "Dog", "Rabbit", "Guinea Pig", "Monkey", "Cat"],
            "phase": ["Phase I", "Phase II", "Phase III", "Phase IV", "Preclinical"],
            "route": ["Oral", "IV", "IM", "Dermal", "Inhalation", "Subcutaneous"],
            "studytype": ["Toxicology", "Pharmacokinetics", "Safety", "Efficacy"],
        }

        for key, options in select_fields.items():
            if key in field_lower:
                return "select"

        # Default to text
        return "text"

    def _get_field_options(self, field_path: str) -> Optional[List[str]]:
        """Get predefined options for select-type fields."""

        field_lower = field_path.lower()

        options_map = {
            "species": ["Rat", "Mouse", "Dog", "Rabbit", "Guinea Pig", "Monkey", "Cat", "Pig", "Minipig"],
            "clinicalphase": ["Phase I", "Phase II", "Phase III", "Phase IV", "Preclinical"],
            "routeofadministration": ["Oral (gavage)", "Intravenous", "Intramuscular", "Subcutaneous",
                                      "Dermal", "Inhalation", "Transdermal", "Intraperitonal"],
            "studytype": ["Acute Toxicity", "Subacute Toxicity", "Subchronic Toxicity", "Chronic Toxicity",
                          "Pharmacokinetics", "Safety Pharmacology", "Reproductive/Developmental", "Mutagenicity"],
            "jurisdiction": ["USA (FDA)", "Europe (EMA)", "Japan (PMDA)", "China (NMPA)",
                             "Canada (HPFB)", "Australia (TGA)", "Global"],
            "significancelevel": ["p<0.05", "p<0.01", "p<0.001"],
        }

        for key, opts in options_map.items():
            if key in field_lower:
                return opts

        return None

    def _suggest_field_value(self, field_path: str, current_value: Any) -> Optional[str]:
        """Suggest a reasonable value for a field based on context."""

        # If value already exists and is reasonable, no suggestion needed
        if current_value and self._check_field_quality(current_value) == "extracted":
            return None

        # Suggest based on common patterns
        suggestions = {
            "BasicDetails.StudyNo": "GLP-2024-001",
            "BasicDetails.Protocol": "Version 1.0",
            "StudyInfo.ClinicalPhase": "Preclinical",
            "ExperimentalDesign.Species": "Rat",
            "ExperimentalDesign.Strain": "Sprague-Dawley",
            "RegulatoryCompliance.Jurisdiction": "USA (FDA)",
            "DataAnalysis.SignificanceLevel": "p<0.05",
        }

        return suggestions.get(field_path)
