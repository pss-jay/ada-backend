"""
Pydantic models for Altasciences preclinical protocol JSON schema.
Supports Rat, Dog, and Swine species with shared core and species-specific extensions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ============================================================================
# SHARED MODELS (used across all sections)
# ============================================================================

class Address(BaseModel):
    """Address information shared across multiple sections."""
    Street: Optional[str] = ""
    City: Optional[str] = ""
    State: Optional[str] = ""
    ZipCode: Optional[str] = ""
    Country: Optional[str] = ""


class PersonnelBase(BaseModel):
    """Base personnel information structure."""
    Name: Optional[str] = ""
    Credentials: Optional[str] = ""
    CompanyName: Optional[str] = ""
    Phone: Optional[str] = ""
    Email: Optional[str] = ""
    AddressOfTestingFacility: Optional[str] = ""


class PersonnelWithStudyPhases(PersonnelBase):
    """Personnel with study phases (Contributing Scientists, Principal Investigators)."""
    StudyPhases: Optional[List[str]] = []


class CellPhone(BaseModel):
    """Personnel with cell phone field."""
    CellPhone: Optional[str] = ""


class StudyDirector(PersonnelBase, CellPhone):
    """Study Director information."""
    pass


class SponsorRepresentativeBase(PersonnelBase):
    """Base class for sponsor representatives."""
    pass


class DateSubsetItem(BaseModel):
    """Date with subset type and date."""
    subsetType: Optional[str] = ""
    subsetDate: Optional[str] = ""


class Schedule(BaseModel):
    """Study schedule information."""
    AnimalArrivalOrTransferOrAcclimationStartDate: Optional[List[DateSubsetItem]] = []
    AuditedDraftDate: Optional[str] = ""
    CompletionOfInLife: Optional[Dict[str, Any]] = Field(default_factory=dict)
    DosingInitiationDate: Optional[List[DateSubsetItem]] = []
    FinalReportDate: Optional[str] = ""


# ============================================================================
# PERSONNEL SECTION
# ============================================================================

class SDS(BaseModel):
    """Study Director and Sponsor sections."""
    StudyDirector: Optional[StudyDirector] = Field(default_factory=StudyDirector)
    SponsorStudyRepresentative: Optional[List[SponsorRepresentativeBase]] = []
    SponsorStudyMonitor: Optional[List[SponsorRepresentativeBase]] = []
    SponsorRepresentative: Optional[List[SponsorRepresentativeBase]] = []


class Personnel(BaseModel):
    """Complete personnel section."""
    SDS: Optional[SDS] = Field(default_factory=SDS)
    ContributingScientists: Optional[List[PersonnelWithStudyPhases]] = []
    PrincipalInvestigatorAtTestingFacilityEnlistedTestSite: Optional[List[PersonnelWithStudyPhases]] = []
    PrincipalInvestigatorAtSponsorOrSponsorEnlistedTestSite: Optional[List[PersonnelWithStudyPhases]] = []
    PeerReviewPathologist: Optional[List[PersonnelBase]] = []


# ============================================================================
# BASIC DETAILS SECTION
# ============================================================================

class BasicDetails(BaseModel):
    """Basic study details."""
    TestPeriod: Optional[str] = ""
    RecoveryPeriod: Optional[str] = ""
    TestSubject: Optional[str] = ""
    TestArticleName: Optional[str] = ""
    StudyNo: Optional[str] = ""
    SponsorRefNo: Optional[str] = ""
    SponsorName: Optional[str] = ""
    SponsorAddress: Optional[Address] = Field(default_factory=Address)


# ============================================================================
# STUDY INFO SECTION
# ============================================================================

class StudyInfo(BaseModel):
    """Complete study information."""
    Objective: Optional[str] = ""
    ClassOrActionOfTestArticle: Optional[str] = ""
    Route: Optional[str] = ""
    RouteDuration: Optional[str] = ""
    RecoveryPeriod: Optional[str] = ""
    TestArticleNameWithTexicokinecCharacteristics: Optional[str] = ""
    TestSpeciesName: Optional[str] = ""
    TestSpeciesSex: Optional[str] = ""
    Schedule: Optional[Schedule] = Field(default_factory=Schedule)
    # Species-specific (swine only)
    StudyLength: Optional[str] = ""


# ============================================================================
# REGULATORY COMPLIANCE SECTION
# ============================================================================

class RegulatoryCompliance(BaseModel):
    """Regulatory compliance information."""
    GLPOrNonGLP: Optional[str] = ""
    nonGLP: Optional[Dict[str, Any]] = Field(default_factory=dict)
    GLP: Optional[Dict[str, Any]] = Field(default_factory=dict)
    TestSiteCountry_Canada_Europe_UK: Optional[str] = ""
    Canada: Optional[Dict[str, Any]] = Field(default_factory=dict)
    Europe: Optional[Dict[str, Any]] = Field(default_factory=dict)
    UK: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# QUALITY ASSURANCE SECTION
# ============================================================================

class QualityAssurance(BaseModel):
    """Quality assurance information."""
    QATestingFacility: Optional[Dict[str, Any]] = Field(default_factory=dict)
    TestingFacilityEnlistedPrincipalInvestigators: Optional[Dict[str, Any]] = Field(default_factory=dict)
    SponsorOrSponsorEnlistedPrincipalInvestigator: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# TEST MATERIAL SECTION
# ============================================================================

class TestArticle(BaseModel):
    """Test article information."""
    Identification: Optional[str] = ""
    AlternateIdentification: Optional[str] = ""
    LotBatchNo: Optional[str] = ""
    CorrectionFactor: Optional[str] = ""
    Concentration: Optional[str] = ""
    ExpirationOrRetestDate: Optional[str] = ""
    StorageConditions: Optional[str] = ""


class VehicleControl(BaseModel):
    """Vehicle control information."""
    Identification: Optional[str] = ""
    AlternateIdentification: Optional[str] = ""
    LotBatchNo: Optional[str] = ""
    ExpirationOrRetestDate: Optional[str] = ""
    StorageConditions: Optional[str] = ""


class VehicleControlWithComponents(VehicleControl):
    """Vehicle control with vehicle components."""
    VehicleComponents: Optional[List[Dict[str, Any]]] = []


class TestMaterial(BaseModel):
    """Complete test material section."""
    TestArticle: Optional[List[TestArticle]] = []
    VehicleControl1: Optional[VehicleControl] = Field(default_factory=VehicleControl)
    VehicleControl2: Optional[VehicleControlWithComponents] = Field(default_factory=VehicleControlWithComponents)
    TestMaterialPreparation: Optional[Dict[str, Any]] = Field(default_factory=dict)
    StabilityDoseFormulation: Optional[Dict[str, Any]] = Field(default_factory=dict)
    DoseFormulationAnalysis: Optional[Dict[str, Any]] = Field(default_factory=dict)
    TestMaterialInventoryAndDisposition: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# TEST SYSTEM SECTION
# ============================================================================

class TestSystem(BaseModel):
    """Test system (animal model) information."""
    SpeciesStrain: Optional[str] = ""
    Supplier: Optional[str] = ""
    Condition: Optional[str] = ""
    MethodofIdentification: Optional[str] = ""
    TargetBodyWeightRange: Optional[str] = ""
    TargetAgeRage: Optional[str] = ""
    NumberofAnimalsforAcclimation: Optional[str] = ""
    NumberofAnimalsforDosing: Optional[str] = ""


# ============================================================================
# HUSBANDRY SECTION
# ============================================================================

class Husbandry(BaseModel):
    """Animal husbandry information."""
    HousingConditions: Optional[Dict[str, Any]] = Field(default_factory=dict)
    EnvironmentalConditions: Optional[Dict[str, Any]] = Field(default_factory=dict)
    EnvironmentalEnrichment: Optional[Dict[str, Any]] = Field(default_factory=dict)
    DietAndFeeding: Optional[Dict[str, Any]] = Field(default_factory=dict)
    DrinkingWater: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# CLINICAL PATHOLOGY SECTION
# ============================================================================

class ClinicalPathology(BaseModel):
    """Clinical pathology information."""
    SampleCollection: Optional[Dict[str, Any]] = Field(default_factory=dict)
    Urinalysis: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # Species-specific (swine only)
    BoneMarrowSmearsEvaluation: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# COMPUTERIZED SYSTEMS AND ANALYZERS SECTION
# ============================================================================

class ComputerizedSystem(BaseModel):
    """Single computerized system/analyzer."""
    Name: Optional[str] = ""
    Purpose: Optional[str] = ""


class ComputerizedSystemsAndAnalyzers(BaseModel):
    """Computerized systems and analyzers information.

    For rat and dog: uses 'systems' list
    For swine: uses named keys as Dict[str, Any]
    """
    systems: Optional[List[ComputerizedSystem]] = []

    class Config:
        extra = "allow"  # Allow arbitrary keys for swine-specific named systems


# ============================================================================
# ANIMAL CARE COMPLIANCE SECTION
# ============================================================================

class AnimalCareCompliance(BaseModel):
    """Animal care and compliance information."""
    Accreditation: Optional[str] = ""
    Assurance: Optional[str] = ""
    Registration: Optional[str] = ""
    OversightCommittee: Optional[str] = ""


# ============================================================================
# REPORTING SECTION
# ============================================================================

class Reporting(BaseModel):
    """Reporting information."""
    TabulatedDataSummary: Optional[str] = ""
    TablesOnly: Optional[str] = ""
    SENDDatasets: Optional[str] = ""
    # Species-specific (swine only)
    ReportNotes: Optional[str] = ""


# ============================================================================
# ARCHIVAL STORAGE SECTION
# ============================================================================

class ArchivalStorage(BaseModel):
    """Archival storage information."""
    PhysicalArchiveLocation: Optional[str] = ""
    OffSiteArchiveSubcontractorAllowed: Optional[bool] = False
    SponsorContactTiming: Optional[str] = ""
    SponsorContactPurpose: Optional[str] = ""
    specimens: Optional[Dict[str, Any]] = Field(default_factory=dict)
    reserve_samples: Optional[Dict[str, Any]] = Field(default_factory=dict)
    ElectronicDataArchiving: Optional[Dict[str, Any]] = Field(default_factory=dict)
    ProvantisDataArchiving: Optional[Dict[str, Any]] = Field(default_factory=dict)
    OffSiteMaterialArchiving: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# TISSUE COLLECTIONS AND PRESERVATION SECTION
# ============================================================================

class TissueCollectionsandPreservation(BaseModel):
    """Tissue collection and preservation information."""
    injection_sites: Optional[Dict[str, Any]] = Field(default_factory=dict)
    injection_site_location_specification: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# APPROVALS SECTION
# ============================================================================

class Approvals(BaseModel):
    """Study approvals and signatures."""
    sponsor_approval_date: Optional[str] = ""
    approval_method: Optional[str] = ""
    approval_documentation_location: Optional[str] = ""
    Usethefollowingformatwhenapplyingwetsignature: Optional[str] = ""
    wet_signature_name_credentials: Optional[str] = ""
    wet_signature_date: Optional[str] = ""
    Usethefollowingformatwhenapplyingelectronicsignature: Optional[str] = ""
    electronic_signature_name_credentials: Optional[str] = ""
    study_director_role: Optional[str] = ""


# ============================================================================
# EXPERIMENTAL DESIGN SECTION
# ============================================================================

class DoseLevel(BaseModel):
    """Dose level with value and unit."""
    value: Optional[str] = ""
    unit: Optional[str] = ""
    note: Optional[str] = ""


class DoseVolume(BaseModel):
    """Dose volume with value and unit."""
    value: Optional[str] = ""
    unit: Optional[str] = ""
    note: Optional[str] = ""


class DoseConcentration(BaseModel):
    """Dose concentration with value and unit."""
    value: Optional[str] = ""
    unit: Optional[str] = ""
    note: Optional[str] = ""


class PhaseAnimals(BaseModel):
    """Animals in a phase (Females/Males count)."""
    Females: Optional[str] = ""
    Males: Optional[str] = ""


class StudyGroup(BaseModel):
    """A treatment group in the study design."""
    GroupNumber: Optional[str] = ""
    TestMaterial: Optional[str] = ""
    DoseLevel: Optional[DoseLevel] = Field(default_factory=DoseLevel)
    DoseVolume: Optional[DoseVolume] = Field(default_factory=DoseVolume)
    DoseConcentration: Optional[DoseConcentration] = Field(default_factory=DoseConcentration)
    MainPhase: Optional[PhaseAnimals] = Field(default_factory=PhaseAnimals)
    RecoveryPhase: Optional[PhaseAnimals] = Field(default_factory=PhaseAnimals)
    TKPhase: Optional[PhaseAnimals] = Field(default_factory=PhaseAnimals)


class ExperimentalDesign(BaseModel):
    """Complete experimental design section."""
    AnimalSource: Optional[str] = ""
    AcclimationPeriod: Optional[str] = ""
    AcclimationBeforeDosing: Optional[str] = ""
    RandomizationAndAnimalAssignment: Optional[Dict[str, Any]] = Field(default_factory=dict)
    CompanionAnimals: Optional[Dict[str, Any]] = Field(default_factory=dict)
    StudyExperimentalDesign: Optional[Dict[str, Any]] = Field(default_factory=dict)
    AnesthesiaAndAnalgesiaProcedures: Optional[Dict[str, Any]] = Field(default_factory=dict)
    AdministrationofDoseFormulations: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # Rat-specific
    MaskingOrBlinding: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# EXPERIMENTAL OBSERVATIONS AND PROCEDURES SECTION
# ============================================================================

class ExperimentalObservationsandProcedures(BaseModel):
    """Experimental observations and procedures."""
    ApplicableCohorts: Optional[str] = ""
    CohortType: Optional[str] = ""
    IncludeSpares: Optional[str] = ""
    ClinicalObservationsAndExaminations: Optional[Dict[str, Any]] = Field(default_factory=dict)
    ModifiedDraizeScoring: Optional[Dict[str, Any]] = Field(default_factory=dict)
    BodyWeight: Optional[Dict[str, Any]] = Field(default_factory=dict)
    Ophthalmology: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # Rat-specific
    TK_Flag: Optional[str] = ""
    TKParagraph: Optional[Dict[str, Any]] = Field(default_factory=dict)
    FunctionalObservationalBatteryAssessments: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # Dog-specific
    FoodConsumption: Optional[Dict[str, Any]] = Field(default_factory=dict)
    Tonometry: Optional[Dict[str, Any]] = Field(default_factory=dict)
    Electrocardiography: Optional[Dict[str, Any]] = Field(default_factory=dict)
    NeurologicalAssessments: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# LABORATORY ASSESSMENTS SECTION
# ============================================================================

class LaboratoryAssessments(BaseModel):
    """Laboratory assessments information."""
    BloodCollection: Optional[Dict[str, Any]] = Field(default_factory=dict)
    SampleCollection: Optional[Dict[str, Any]] = Field(default_factory=dict)
    BioanalysisAndToxicokineticEvaluation: Optional[Dict[str, Any]] = Field(default_factory=dict)
    ImmunophenotypingOfPeripheralBloodAnalysis: Optional[Dict[str, Any]] = Field(default_factory=dict)
    AntiDrugAntibodySampleProcessingandAnalysis: Optional[Dict[str, Any]] = Field(default_factory=dict)
    CytokinesSampleProcessingAndAnalysis: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # Rat-specific
    Cohorts: Optional[Dict[str, Any]] = Field(default_factory=dict)
    OtherPlasmaorSerumforPotentialAnalysis: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # Dog-specific
    ThyroidAndTestosteroneEvaluation: Optional[Dict[str, Any]] = Field(default_factory=dict)
    TroponinIAndC_ReactiveProteinEvaluation: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# TERMINAL PROCEDURES AND PATHOLOGY SECTION
# ============================================================================

class TerminalProceduresandPathology(BaseModel):
    """Terminal procedures and pathology information."""
    NecropsyStatus: Optional[Dict[str, Any]] = Field(default_factory=dict)
    InLifeCompletion: Optional[Dict[str, Any]] = Field(default_factory=dict)
    ScheduledEuthanasia: Optional[Dict[str, Any]] = Field(default_factory=dict)
    UnscheduledEuthanasia: Optional[Dict[str, Any]] = Field(default_factory=dict)
    FoundDead: Optional[Dict[str, Any]] = Field(default_factory=dict)
    HistologyandHistopathology: Optional[Dict[str, Any]] = Field(default_factory=dict)
    UE_and_FD_Sections: Optional[Dict[str, Any]] = Field(default_factory=dict)
    OrganWeights: Optional[Dict[str, Any]] = Field(default_factory=dict)
    PathologyPeerReview: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # Rat-specific
    TissueCollectionForBiodistribution: Optional[Dict[str, Any]] = Field(default_factory=dict)
    UEandFDSections: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# DATA ANALYSIS SECTION
# ============================================================================

class DataAnalysis(BaseModel):
    """Data analysis information."""
    DataPresentationOptions: Optional[Dict[str, Any]] = Field(default_factory=dict)
    StatisticalAnalyses: Optional[Dict[str, Any]] = Field(default_factory=dict)
    # Rat-specific
    NumericalDataHandling: Optional[Dict[str, Any]] = Field(default_factory=dict)
    SpecialScoringMethods: Optional[Dict[str, Any]] = Field(default_factory=dict)
    Non_NumericalDataHandling: Optional[Dict[str, Any]] = Field(default_factory=dict)
    ExcludedSubjects: Optional[Dict[str, Any]] = Field(default_factory=dict)
    StatisticalAnalysisforFunctionalObservationalBattery: Optional[Dict[str, Any]] = Field(default_factory=dict)


# ============================================================================
# STUDY DESIGN GUIDANCE SECTION
# ============================================================================

class StudyDesignGuidance(BaseModel):
    """Study design guidance information."""
    Non_GLP: Optional[str] = ""
    NonGLP: Optional[str] = ""
    GLP: Optional[str] = ""
    ICHS3a: Optional[str] = ""
    ICHS4: Optional[str] = ""
    ICHS6_R1: Optional[str] = ""
    ICHS6R1: Optional[str] = ""
    ICHS7A: Optional[str] = ""
    ICHS7B: Optional[str] = ""
    ICHS8: Optional[str] = ""
    species_selected: Optional[str] = ""
    reason_for_species_selection: Optional[str] = ""
    growth_characteristics: Optional[str] = ""
    disease_status: Optional[str] = ""
    spontaneous_abnormality_level: Optional[str] = ""
    number_of_animals: Optional[str] = ""
    justification_for_animal_number: Optional[str] = ""
    route_of_administration: Optional[str] = ""
    justification_for_route: Optional[str] = ""
    dose_levels: Optional[str] = ""
    justification_for_dose_levels: Optional[str] = ""
    Specifypeciesprovidesomelevelofdetailandincludereferencesifavailable: Optional[str] = ""
    frequency_of_administration: Optional[str] = ""
    justification_for_frequency: Optional[str] = ""


# ============================================================================
# COMMON PROTOCOL MODEL (all shared fields)
# ============================================================================

class CommonProtocolModel(BaseModel):
    """Base protocol model with all common fields shared across species."""

    StudyNo: Optional[str] = ""
    BasicDetails: Optional[BasicDetails] = Field(default_factory=BasicDetails)
    StudyInfo: Optional[StudyInfo] = Field(default_factory=StudyInfo)
    Personnel: Optional[Personnel] = Field(default_factory=Personnel)
    RegulatoryCompliance: Optional[RegulatoryCompliance] = Field(default_factory=RegulatoryCompliance)
    QualityAssurance: Optional[QualityAssurance] = Field(default_factory=QualityAssurance)
    TestMaterial: Optional[TestMaterial] = Field(default_factory=TestMaterial)
    TestSystem: Optional[TestSystem] = Field(default_factory=TestSystem)
    Husbandry: Optional[Husbandry] = Field(default_factory=Husbandry)
    ClinicalPathology: Optional[ClinicalPathology] = Field(default_factory=ClinicalPathology)
    ComputerizedSystemsandAnalyzers: Optional[ComputerizedSystemsAndAnalyzers] = Field(
        default_factory=ComputerizedSystemsAndAnalyzers
    )
    AnimalCareCompliance: Optional[AnimalCareCompliance] = Field(default_factory=AnimalCareCompliance)
    Reporting: Optional[Reporting] = Field(default_factory=Reporting)
    ArchivalStorage: Optional[ArchivalStorage] = Field(default_factory=ArchivalStorage)
    References: Optional[str] = ""
    TissueCollectionsandPreservation: Optional[TissueCollectionsandPreservation] = Field(
        default_factory=TissueCollectionsandPreservation
    )
    Approvals: Optional[Approvals] = Field(default_factory=Approvals)
    ExperimentalDesign: Optional[ExperimentalDesign] = Field(default_factory=ExperimentalDesign)
    ExperimentalObservationsandProcedures: Optional[ExperimentalObservationsandProcedures] = Field(
        default_factory=ExperimentalObservationsandProcedures
    )
    LaboratoryAssessments: Optional[LaboratoryAssessments] = Field(default_factory=LaboratoryAssessments)
    TerminalProceduresandPathology: Optional[TerminalProceduresandPathology] = Field(
        default_factory=TerminalProceduresandPathology
    )
    DataAnalysis: Optional[DataAnalysis] = Field(default_factory=DataAnalysis)
    StudyDesignGuidance: Optional[StudyDesignGuidance] = Field(default_factory=StudyDesignGuidance)


# ============================================================================
# SPECIES-SPECIFIC EXTENSIONS
# ============================================================================

class RatExtensions(BaseModel):
    """Rat-specific extensions (inherits CommonProtocolModel)."""
    pass  # All rat-specific fields are already in the base model via optional fields


class DogExtensions(BaseModel):
    """Dog-specific extensions (inherits CommonProtocolModel)."""
    pass  # All dog-specific fields are already in the base model via optional fields


class SwineExtensions(BaseModel):
    """Swine-specific extensions (inherits CommonProtocolModel)."""
    StudyCompanyName: Optional[str] = ""


# ============================================================================
# COMBINED SPECIES-SPECIFIC MODELS
# ============================================================================

class RatProtocolModel(CommonProtocolModel, RatExtensions):
    """Complete protocol model for Rat studies."""
    pass


class DogProtocolModel(CommonProtocolModel, DogExtensions):
    """Complete protocol model for Dog studies."""
    pass


class SwineProtocolModel(CommonProtocolModel, SwineExtensions):
    """Complete protocol model for Swine studies."""
    pass


# ============================================================================
# SPECIES MODEL MAPPING
# ============================================================================

SPECIES_MODEL_MAP: Dict[str, Any] = {
    "rat": RatProtocolModel,
    "dog": DogProtocolModel,
    "swine": SwineProtocolModel,
}

__all__ = [
    "CommonProtocolModel",
    "RatProtocolModel",
    "DogProtocolModel",
    "SwineProtocolModel",
    "RatExtensions",
    "DogExtensions",
    "SwineExtensions",
    "SPECIES_MODEL_MAP",
    # Supporting models
    "Address",
    "Personnel",
    "BasicDetails",
    "StudyInfo",
    "RegulatoryCompliance",
    "QualityAssurance",
    "TestMaterial",
    "TestSystem",
    "Husbandry",
    "ClinicalPathology",
    "ComputerizedSystemsAndAnalyzers",
    "AnimalCareCompliance",
    "Reporting",
    "ArchivalStorage",
    "TissueCollectionsandPreservation",
    "Approvals",
    "ExperimentalDesign",
    "ExperimentalObservationsandProcedures",
    "LaboratoryAssessments",
    "TerminalProceduresandPathology",
    "DataAnalysis",
    "StudyDesignGuidance",
]
