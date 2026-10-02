"""Default plugin set. To add a feature, write a plugin and register it here."""
from ..core.registry import Registry
from .drums import DrumTranscriber
from .drums_adtof import ADTOFDrumTranscriber
from .harmony import HarmonyAnalyzer
from .rhythm import LibrosaBeatTracker
from .separation import DemucsSeparator
from .structure import StructureAnalyzer
from .tab import TUNINGS, TabArranger
from .transcription import PITCHED, BasicPitchTranscriber


def default_registry(device: str | None = None) -> Registry:
    reg = Registry()
    reg.register(DemucsSeparator(device=device))
    reg.register(LibrosaBeatTracker())
    reg.register(HarmonyAnalyzer())
    reg.register(StructureAnalyzer())
    reg.register(ADTOFDrumTranscriber())   # preferred when installed
    reg.register(DrumTranscriber())        # fallback: NMF + rules, no extra install
    for inst in PITCHED:
        reg.register(BasicPitchTranscriber(inst))
    for inst in TUNINGS:
        reg.register(TabArranger(inst))
    return reg
