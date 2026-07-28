"""Парсеры бланков лабораторий и других медицинских документов.

Порядок в PARSER_CHAIN важен: специализированные парсеры пробуются первыми,
GenericParser стоит последним и разбирает всё, что никто не опознал.
"""

from .base import (
    BaseParser,
    GenericParser,
    ParsedDocument,
    ParsedMedication,
    ParsedResult,
    parse_date,
    parse_reference,
    parse_value,
)
from .cgemo import CgemoParser
from .femoflor import FemoflorParser
from .gemotest import GemotestParser
from .inovalys import InovalysParser
from .prescription import PrescriptionParser
from .invitro import InvitroParser
from .pervyj_doctor import PervyjDoctorParser
from .smclinic import SmClinicParser
from .smclinic_diag import SmClinicDiagParser
from .vet import VetlabParser, VetunionParser

PARSER_CHAIN = [
    PrescriptionParser(),
    FemoflorParser(),
    GemotestParser(),
    CgemoParser(),
    PervyjDoctorParser(),
    SmClinicParser(),
    SmClinicDiagParser(),
    InovalysParser(),
    VetunionParser(),
    VetlabParser(),
    InvitroParser(),
    GenericParser(),
]

__all__ = [
    "BaseParser",
    "GenericParser",
    "CgemoParser",
    "FemoflorParser",
    "GemotestParser",
    "InovalysParser",
    "PrescriptionParser",
    "InvitroParser",
    "PervyjDoctorParser",
    "SmClinicParser",
    "SmClinicDiagParser",
    "VetlabParser",
    "VetunionParser",
    "ParsedDocument",
    "ParsedMedication",
    "ParsedResult",
    "PARSER_CHAIN",
    "parse_date",
    "parse_reference",
    "parse_value",
]
