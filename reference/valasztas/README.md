# valasztas — the electoral districts by settlement

`oevk_telepules.json` — the 106 egyéni választókerület (OEVK) with their seats and the settlements each covers,
taken from the annex that defines them: 2011. évi CCIII. törvény az országgyűlési képviselők választásáról,
2. számú melléklet, in the consolidated text in force from 2024-12-31 (Nemzeti Jogszabálytár, njt.hu),
fetched 2026-08-19 and parsed by hand-checked rules (`scripts/` has no fetcher for it: the annex changes only by
statute). Every whole settlement is listed under its OEVK; where a city or a Budapest district is split between
districts, the annex draws the boundary by streets — those entries carry `partial: true`, and "Ki a képviselőm?"
then lists every candidate OEVK and says the address decides.

Not in here: streets and house numbers (the annex has them as prose; a per-address lookup is the NVI's own service).

Naming: the API's MP records still print the pre-2020 county name "Csongrád" in `valasztokerulet`; the annex says "Csongrád-Csanád". The builder joins them through one alias (`COUNTY_ALIAS` in scripts/build_site.py) and shows the current name.
