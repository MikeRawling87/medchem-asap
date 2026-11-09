"""Free, rule-based med chem / drug discovery relevance scoring.

score >= RELEVANT  -> shown by default
score >= BORDERLINE -> shown with "include borderline"
Core med chem journals are always relevant.
Edit the term lists below to tune what gets through.
"""
import re

RELEVANT = 7
BORDERLINE = 4

# (pattern, weight). Patterns are regexes matched case-insensitively on title+abstract.
# Title matches count double.
POSITIVE = [
    # explicit drug discovery language
    (r"medicinal chemistry", 5), (r"drug discovery", 5), (r"drug design", 4),
    (r"drug[- ]like", 4), (r"druggab", 4), (r"drug candidate", 5), (r"clinical candidate", 5),
    (r"lead optimi[sz]ation", 5), (r"hit[- ]to[- ]lead", 5), (r"lead compound", 4),
    (r"structure[–-]activity relationship", 5), (r"\bSAR\b", 4), (r"\bSPR\b", 2),
    (r"preclinical", 4), (r"clinical trial", 3), (r"\bIND\b-enabling", 5),
    (r"pharmaceutical", 3), (r"\bAPI\b", 1), (r"active pharmaceutical ingredient", 4),
    (r"therapeutic", 1), (r"\bdrugs?\b", 2), (r"pharmacolog", 3), (r"pharmacophore", 4),
    (r"bioisoster", 5), (r"scaffold hop", 5), (r"privileged scaffold", 4),
    # potency / assay language
    (r"\bIC50\b|\bIC₅₀\b|IC<sub>50", 4), (r"\bEC50\b|\bEC₅₀\b|EC<sub>50", 4),
    (r"\bK[id]\b|\bK<sub>[id]", 2), (r"nanomolar|(?-i:\bnM\b)", 3), (r"\bpotent\b|potently", 3), (r"potency", 3),
    (r"selectiv\w* (inhibitor|over|against|for)", 3), (r"inhibitors?\b", 3), (r"inhibition of", 2),
    (r"agonist", 2), (r"antagonist", 2), (r"allosteric", 3), (r"modulators?\b", 2),
    (r"target engagement", 4), (r"cellular activity", 3), (r"binding affinity", 3),
    (r"\bk(inact)?\s*/\s*K[iI]\b|kinact", 5),
    # modalities
    (r"PROTAC", 5), (r"degrader", 5), (r"molecular glue", 5), (r"targeted protein degradation", 5),
    (r"covalent (inhibitor|ligand|drug|probe|warhead)", 5), (r"warhead", 3), (r"SuFEx", 2),
    (r"fragment[- ]based", 4), (r"DNA[- ]encoded librar", 5), (r"\bDEL\b", 2),
    (r"macrocycl\w* (peptide|inhibitor|drug)", 4), (r"peptide drug|peptide therapeut", 4),
    (r"antibody[–-]drug conjugate|\bADCs?\b", 4), (r"radioligand|radiopharm", 4),
    (r"chemical probe", 4), (r"chemoproteomic", 4), (r"activity[- ]based protein profiling|\bABPP\b", 4),
    (r"prodrug", 4), (r"\bRNA[- ]targeting small molecule", 5), (r"small[- ]molecule", 3),
    # ADME / DMPK / tox
    (r"\bADMET?\b", 5), (r"\bDMPK\b", 5), (r"pharmacokinetic", 5), (r"bioavailab", 4),
    (r"metabolic stability", 4), (r"microsom", 3), (r"hERG", 4), (r"clearance", 2),
    (r"permeabilit", 2), (r"solubility", 1), (r"cytotoxic", 2), (r"toxicity", 2), (r"in vivo efficacy", 5),
    (r"xenograft", 4), (r"oral(ly)? (bioavailable|active|dosed|efficacy)", 5), (r"\bPK/PD\b|\bPK\b", 3),
    (r"blood[–-]brain barrier|brain[- ]penetra|CNS[- ]penetra", 4),
    # target classes & disease
    (r"kinase", 3), (r"\bGPCR", 4), (r"protease", 2), (r"ion channel", 3), (r"nuclear receptor", 3),
    (r"\bE3 ligase|cereblon|\bVHL\b", 4), (r"\bKRAS\b|\bRAS\b", 4), (r"bromodomain|\bBRD4\b", 4),
    (r"epigenetic", 2), (r"methyltransferase", 3), (r"\bHDAC", 4), (r"\bPARP", 4), (r"\bBTK\b|\bEGFR\b|\bCDK\d", 4),
    (r"cancer|oncolog|tumou?r|leukemi|leukaemi|\bAML\b", 1), (r"anticancer|antitumou?r|antiproliferat", 4),
    (r"antibacterial|antibiotic|antimicrobial|antifungal|antimalarial|antiparasit|antitubercul", 4),
    (r"antiviral|SARS-CoV-2|\bHIV\b|influenza", 2), (r"neurodegenerat|Alzheimer|Parkinson|\bALS\b", 1),
    (r"inflammat", 1), (r"fibrosis|diabet|obesity|cardiovascular", 1), (r"analgesic|\bpain\b", 2),
    # chemistry that matters to med chemists
    (r"late[- ]stage functionali[sz]ation", 5), (r"late[- ]stage", 2), (r"saturated bioisoster", 5),
    (r"bicyclo\[1\.1\.1\]pentane|\bBCPs?\b|cubane|azetidine|oxetane|spirocycl", 3),
    (r"\bFsp3\b|sp3[- ]rich|three[- ]dimensional fragment", 4),
    (r"heteroaryl|heteroarene|\bheterocycl", 2), (r"N-heterocycl", 2), (r"deuterat", 2),
    (r"fluorinat|trifluoromethyl|difluoromethyl|\bCF3\b|\bSF5\b", 1),
    (r"C[–-]H (functionali[sz]ation|activation|amination|arylation)", 1), (r"amide bond", 1),
    (r"parallel synthesis|high[- ]throughput experimentation|\bHTE\b", 3), (r"library synthesis|compound librar", 3),
    (r"pharmaceutically relevant|drug molecules|bioactive molecules|biologically active", 4),
    (r"bioactiv", 2), (r"natural product", 1), (r"total synthesis", 0),
    (r"process (chemistry|development)|kilogram|multikilogram|scale[- ]up", 2),
    # computation
    (r"docking|virtual screening|free energy perturbation|\bFEP\b", 3), (r"co-?folding|AlphaFold", 3),
    (r"machine learning|deep learning|generative model", 1), (r"\bQSAR\b", 4), (r"ligand[- ]based|structure[- ]based (drug )?design", 4),
    (r"cryo-?EM|crystal structure of .* (in complex|bound)", 2), (r"co-?crystal structure", 3),
    (r"chemical biology", 2), (r"enzyme", 1), (r"\breceptors?\b", 1), (r"protein[–-]protein interaction", 3),
]

NEGATIVE = [
    (r"perovskite", -6), (r"photovoltaic|solar cell", -6), (r"lithium[- ]ion|batter(y|ies)|anode|cathode|electrolyte", -5),
    (r"supercapacitor|thermoelectric", -6), (r"\bMOFs?\b|metal[–-]organic framework|covalent organic framework|\bCOFs?\b", -3),
    (r"\bCO2 (reduction|capture)|water splitting|oxygen evolution|hydrogen evolution|(?-i:\bOER\b|\bHER\b)|nitrogen reduction", -5),
    (r"polymeri[sz]ation|copolymer|thermoplastic|elastomer", -3), (r"semiconductor|transistor|optoelectron|\bO?LEDs?\b", -4),
    (r"astrophys|astronom|interstellar|atmospheric|climate|geolog|\bminerals?\b|Miocene|ocean|sediment", -6),
    (r"plasmon|quantum dot|nanosheet|2D material|graphene", -3), (r"zeolite|heterogeneous catalys", -3),
    (r"superconduct|magnetism|magnetic (material|order|anisotrop)|single[- ]molecule magnet|spin[- ]crossover|ferroelectric", -4), (r"combustion|fuel|biomass|lignin", -3),
    (r"nanozyme|nanoparticle|nanomaterial|nanocarrier|nanocluster|hydrogel|microsphere|extracellular vesicle", -2),
    (r"\bcorrection\b to|^correction|retraction|expression of concern", -10),
]

TOPICS = {
    "Degraders & glues": r"PROTAC|degrader|molecular glue|targeted protein degradation|cereblon|\bVHL\b|E3 ligase",
    "Covalent": r"covalent|warhead|kinact|SuFEx|electrophil\w* (fragment|ligand)|acrylamide",
    "Kinases": r"kinase",
    "GPCRs & channels": r"\bGPCR|G protein[- ]coupled|ion channel|agonist|antagonist",
    "Epigenetics & RNA": r"epigenetic|bromodomain|HDAC|methyltransferase|RNA[- ]targeting|RNA[- ]binding",
    "Oncology": r"cancer|oncolog|tumou?r|leukemi|leukaemi|\bAML\b|KRAS|\bRAS\b|xenograft|antiproliferat",
    "Neuro": r"neuro|Alzheimer|Parkinson|\bALS\b|\bCNS\b|brain",
    "Anti-infectives": r"antibacterial|antibiotic|antimicrobial|antifungal|antimalarial|antiviral|antiparasit|tubercul|SARS-CoV-2|\bHIV\b",
    "Immunology & inflammation": r"inflammat|immun|autoimmun|\bSTING\b|\bNLRP3\b|cytokine",
    "ADME/PK & tox": r"\bADMET?\b|DMPK|pharmacokinetic|bioavailab|metabolic stability|microsom|hERG|clearance|permeabilit|toxicit",
    "Comp chem & AI": r"docking|virtual screening|free energy|\bFEP\b|machine learning|deep learning|generative|AlphaFold|co-?folding|QSAR|molecular dynamics",
    "Fragments & DEL": r"fragment[- ]based|fragment screen|DNA[- ]encoded|\bDEL\b",
    "Peptides & macrocycles": r"peptide|macrocycl",
    "Chemical biology & probes": r"chemical probe|chemoproteomic|ABPP|activity[- ]based|photoaffinity|target identification|chemical biology",
    "Bioisosteres & 3D scaffolds": r"bioisoster|bicyclo\[|\bBCP|cubane|azetidine|oxetane|spirocycl|Fsp3|sp3[- ]rich|scaffold hop",
    "Synthetic methods": r"late[- ]stage|C[–-]H (functionali|activation|amination|arylation)|cross[- ]coupling|photoredox|electrochem|enantioselective|asymmetric|catalys",
    "Process & scale-up": r"process (chemistry|development)|kilogram|scale[- ]up|manufactur|flow chemistry|continuous flow",
    "Natural products": r"natural product|isolated from|alkaloid|terpen|polyketide|biosynthe",
}

_pos = [(re.compile(p, re.I), w) for p, w in POSITIVE]
_neg = [(re.compile(p, re.I), w) for p, w in NEGATIVE]
_topics = {k: re.compile(v, re.I) for k, v in TOPICS.items()}


def score(title, abstract, core=False):
    title = title or ""
    abstract = abstract or ""
    s = 0
    for rx, w in _pos:
        if w <= 0:
            continue
        if rx.search(title):
            s += 2 * w
        elif rx.search(abstract):
            s += w
    for rx, w in _neg:
        if rx.search(title):
            s += 2 * w
        elif rx.search(abstract):
            s += w
    # cap so a single verbose abstract can't dominate sorting
    s = max(-20, min(s, 40))
    if core:
        s = max(s, RELEVANT + 6)
    return s


def topics(title, abstract):
    text = f"{title or ''} {abstract or ''}"
    return [k for k, rx in _topics.items() if rx.search(text)]
