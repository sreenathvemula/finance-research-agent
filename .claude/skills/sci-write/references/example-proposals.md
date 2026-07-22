# Real application library — annotated links + extracted patterns

Openly shared, real proposals located July 2026. Neither DFG nor Humboldt publishes funded
proposals, and no full-text seismology-specific MSCA/Humboldt proposal exists on the open web —
the nearest-field examples below are structurally transferable. GFZ's research office, KoWi, and
EGU/AGU early-career networks hold discipline-matched examples on request.

## A. Full texts worth studying (readable online)

| Proposal | Scheme | Outcome | Field | Link |
|---|---|---|---|---|
| Shannon Chance, MSCA-IF — posted section by section, BOTH the 2015 rejected and 2016 funded versions | MSCA-IF | **Funded** (2016, after revision) | Engineering education | https://shannonchance.net/category/marie-curie-fellowship/msca-example-proposal/ |
| Daniel Graziotin, "Software quality information needs" | Humboldt Research Fellowship | **Funded** | Software engineering | https://doi.org/10.3897/rio.2.e8865 |
| Jens Joschinski | DFG postdoc grant | **Funded** | Ecology | https://doi.org/10.3897/rio.2.e9580 |
| Neelakshi Joshi, socio-natural hazards from unplanned Himalayan urbanisation | DLGS 3-yr scholarship | **Funded** | **Disaster risk / urbanisation** | https://zenodo.org/records/7837801 (CC-BY) |
| Stall et al., open-science communities (incl. seismology/FDSN) | NSF GEO OSE | Submitted 2025 | Geoscience data / **seismology** | https://zenodo.org/records/14887843 |
| Gatti & Lopez-Caballero, hybrid ML + high-fidelity earthquake simulation (nuclear sites) | GENCI/DARI HPC allocation | **Granted** (renewal) | **Seismic hazard + ML** | https://zenodo.org/records/17313460 (companion: https://zenodo.org/records/17408003) |
| Rincón-Cardeño et al., PINNs in seismology | PhD research proposal | n/a | **ML + wave propagation** | https://zenodo.org/records/19893341 |
| Emery, full funded ERC Starting Grant B1+B2 | ERC StG 2021 | **Funded** | Sociology | https://zenodo.org/records/6860359 |
| Project Pythia/Pangeo | NSF GEO OSE | **Funded** | Open geoscience computing | https://zenodo.org/records/8184298 |
| Flores, NSF CAREER (hydroclimate modeling) | NSF CAREER | **Funded** | Geoscience/ML-adjacent | https://zenodo.org/records/3236624 |
| Johnson & Webb, East Gobi Fault Zone | NSF Tectonics | **Funded** | Tectonics | https://cdn.serc.carleton.edu/files/NAGTWorkshops/earlycareer/research/johnson_amp_webb_nsf.pdf |
| Till, CAREER: triggering volcanic eruptions | NSF CAREER | **Funded** | Volcanic hazard | via https://serc.carleton.edu/NAGTWorkshops/earlycareer/research/NSFgrants.html |

**With reviewer feedback attached (study the scoring language):**
- Dalgleish, MSCA Global PF — **rejected at 75.6%, official evaluation report included**:
  https://zenodo.org/records/10774981
- Fajardo Ortiz, "NECESSARIES", MSCA-PF 2023 — unfunded, **Part B1 + Evaluation Summary Report**
  in the PDF (needs manual download): https://zenodo.org/records/10716112
- Chance's blog discusses exactly what evaluators criticized in the rejected 2015 version
  (work-plan feasibility in 24 months) and how the funded 2016 version fixed it.

**Indexes for more:** https://www.ogrants.org (302 proposals, filter by funder);
Zenodo search with resource subtype "proposal"; SERC/NAGT funded-NSF-geoscience collection
(https://serc.carleton.edu/NAGTWorkshops/earlycareer/research/NSFgrants.html, ~30 full texts).

## B. Patterns extracted from close reading

**Openings (first half-page decides the read):**
- Pair a recognisable stakes-anchor with a quantified trend immediately. Joshi: three named
  disasters (2015 Nepal earthquake, 2014 Srinagar floods, 2013 Uttarakhand cloudburst) + a
  census table of Himalayan urbanisation. Stall: 75% of data citations go to generalist
  repositories, un-FAIR data costs €10.2B/yr. Gatti: own prior simulation of the
  Kashiwazaki-Kariwa nuclear plant in the 2007 Mw 6.6 Niigata earthquake, with domain size and
  frequency numbers — credibility and stakes in one move.
- Joshi's funnel is the model for a disaster proposal: region-scale sweep → demographic pivot
  with data → collision (unplanned growth onto hazardous slopes) → why neglected (chronic small
  events never enter disaster records, so nobody studies them).

**Literature review → gap:**
- Converge in nested steps, each ending on what remains unknown. Joshi: global policy frames it
  → attention is on coastal megacities, mountains peripheral → the hazard class itself is
  neglected (with an authority definition) → even Himalayan studies cover building vulnerability,
  not socio-natural hazard drivers. The gap arrives triply-nested and inevitable.
- Respectful gap-framing for a mature field (Stall on seismology): the community led data
  standards for four decades (FDSN), BUT the standards predate FAIR and cannot serve ML-era
  machine-to-machine use. Honor the field, then show where it stops.
- Graziotin (Humboldt): funnel ends in ONE explicit gap sentence, then a new concept is defined
  on a cited foundation. Chance (MSCA): gap as "authoritative bodies demand change, yet practice
  hasn't moved" — proves the problem is live, not just unstudied.

**Objectives/questions:**
- Joshi (strongest template): one bolded overall question → 5 numbered sub-questions, each keyed
  to one analytic dimension → 5 numbered falsifiable hypotheses in 1:1 correspondence.
- Graziotin: one broad question + exactly three sub-questions in the abstract; four short titled
  objectives, each one paragraph, each mapping to a work package.
- Gatti: one capability objective + one quantified deliverable (30 scenario realizations, the
  number justified by analogy to a published study).
- Rincón: four questions ordered as a ladder — survey → benchmark → measurable comparative claim
  → scaling to uncontrolled settings.
- Verbs: quantify/characterize/benchmark, not explore/investigate. Objectives independent enough
  that one failing doesn't kill the rest.

**Methods/work plan:**
- Question → data → method → expected outcome as a matrix table (Joshi: per-RQ rows down to
  "~750–800 houses in one ward, house-level GIS on a contour base map"). Per-step durations
  (Stall: 1 + 3 + 6 + 5 months per discipline). Every strong proposal closes the section with a
  Gantt chart.
- State scope exclusions inside the plan ("geotechnical investigations are beyond scope") — it
  reads as discipline, not weakness.
- Host fit argued through the science: Graziotin names the host institute's tools inside the
  methodology; Chance quantifies the host (EU-funding totals, MSCA project counts).

**Impact:**
- Split science advance from societal path, each concrete. Joshi: recommendations addressed to
  named actors (municipality, NGOs, developers) + the conceptual lever that socio-natural hazards,
  unlike natural ones, are avoidable through land management. Stall: counterfactual framing —
  without this, the status quo persists (the "cost of inaction" as a persuasion device). Gatti:
  simulation → ML surrogate → operational seismic safety of nuclear sites and cities.
- Keep impact claims repeatable: a reviewer should be able to restate them in one sentence to
  the panel.

**Risk/feasibility:**
- Never omitted. Three working modes: scoping + preliminary work absorbed into the plan (Joshi);
  documented pre-engagement + "confidence in success" + quantified prior-award outcomes (Stall);
  benchmarked feasibility with explicit fallbacks (Gatti: h- vs p-refinement). Rincón converts
  the main risk (ML on noisy real data) into the research question itself.
