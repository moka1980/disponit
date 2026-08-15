// GENERERT av tools/gen_katalog.py fra
// prototype/AI-bedriftsagent-prototype-v7.html — IKKE rediger for hånd.
//
// Modulkatalogen er produktomfanget: 45 moduler i 11 områder over fire
// faser. Den er OFFENTLIG informasjon (hva vi tilbyr), i motsetning til
// tenantdata, som aldri skal ligge i en anonymt nedlastbar fil.
//
// Navnene ligger i locales/ som `site.katalog.m<n>.navn` og
// `site.omrade.<slug>` — teksten er oversettelse, strukturen er data.

export const KATALOG = [
  { n: 1, omrade: "plattform_og_sikkerhet", fase: 1 },
  { n: 2, omrade: "plattform_og_sikkerhet", fase: 1 },
  { n: 3, omrade: "data_og_kunnskap", fase: 1 },
  { n: 4, omrade: "data_og_kunnskap", fase: 1 },
  { n: 5, omrade: "dokument_og_kommunikasjon", fase: 1 },
  { n: 6, omrade: "dokument_og_kommunikasjon", fase: 1 },
  { n: 7, omrade: "samarbeid_og_hr", fase: 1 },
  { n: 8, omrade: "samarbeid_og_hr", fase: 1 },
  { n: 9, omrade: "data_og_kunnskap", fase: 1 },
  { n: 10, omrade: "it_og_drift", fase: 1 },
  { n: 11, omrade: "it_og_drift", fase: 1 },
  { n: 12, omrade: "it_og_drift", fase: 1 },
  { n: 13, omrade: "okonomi", fase: 2 },
  { n: 14, omrade: "okonomi", fase: 2 },
  { n: 15, omrade: "okonomi", fase: 2 },
  { n: 16, omrade: "analyse_og_ledelse", fase: 2 },
  { n: 17, omrade: "kunde_og_salg", fase: 2 },
  { n: 18, omrade: "kunde_og_salg", fase: 2 },
  { n: 19, omrade: "kunde_og_salg", fase: 2 },
  { n: 20, omrade: "markedsforing", fase: 2 },
  { n: 21, omrade: "juridisk_og_compliance", fase: 2 },
  { n: 22, omrade: "it_og_drift", fase: 2 },
  { n: 23, omrade: "okonomi", fase: 3 },
  { n: 24, omrade: "innkjop_og_logistikk", fase: 3 },
  { n: 25, omrade: "kunde_og_salg", fase: 3 },
  { n: 26, omrade: "kunde_og_salg", fase: 3 },
  { n: 27, omrade: "innkjop_og_logistikk", fase: 3 },
  { n: 28, omrade: "innkjop_og_logistikk", fase: 3 },
  { n: 29, omrade: "plattform_og_sikkerhet", fase: 3 },
  { n: 30, omrade: "juridisk_og_compliance", fase: 3 },
  { n: 31, omrade: "plattform_og_sikkerhet", fase: 3 },
  { n: 32, omrade: "juridisk_og_compliance", fase: 4 },
  { n: 33, omrade: "analyse_og_ledelse", fase: 4 },
  { n: 34, omrade: "juridisk_og_compliance", fase: 4 },
  { n: 35, omrade: "it_og_drift", fase: 4 },
  { n: 36, omrade: "analyse_og_ledelse", fase: 4 },
  { n: 37, omrade: "plattform_og_sikkerhet", fase: 1 },
  { n: 38, omrade: "plattform_og_sikkerhet", fase: 1 },
  { n: 39, omrade: "okonomi", fase: 2 },
  { n: 40, omrade: "samarbeid_og_hr", fase: 2 },
  { n: 41, omrade: "okonomi", fase: 3 },
  { n: 42, omrade: "okonomi", fase: 3 },
  { n: 43, omrade: "kunde_og_salg", fase: 3 },
  { n: 44, omrade: "markedsforing", fase: 3 },
  { n: 45, omrade: "analyse_og_ledelse", fase: 4 },
];

// Områdene i fast rekkefølge, med modulene sine.
export const OMRADER = [
  { id: "analyse_og_ledelse", moduler: [16, 33, 36, 45] },
  { id: "data_og_kunnskap", moduler: [3, 4, 9] },
  { id: "dokument_og_kommunikasjon", moduler: [5, 6] },
  { id: "it_og_drift", moduler: [10, 11, 12, 22, 35] },
  { id: "innkjop_og_logistikk", moduler: [24, 27, 28] },
  { id: "juridisk_og_compliance", moduler: [21, 30, 32, 34] },
  { id: "kunde_og_salg", moduler: [17, 18, 19, 25, 26, 43] },
  { id: "markedsforing", moduler: [20, 44] },
  { id: "plattform_og_sikkerhet", moduler: [1, 2, 29, 31, 37, 38] },
  { id: "samarbeid_og_hr", moduler: [7, 8, 40] },
  { id: "okonomi", moduler: [13, 14, 15, 23, 39, 41, 42] },
];

export const KATALOG_ANTALL = KATALOG.length;
