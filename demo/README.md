# Demo data

`people-demo.csv` is illustrative, not catalogue data: 20 people linked to Basque culture with a
made-up id, a type, an English description and, for three of them, an existing Wikidata link
(two right, Goya's deliberately wrong). Against ECHOLOT (2026-10-27) it shows the three kinds of
result the review is for. In a dry run on 2026-10-27 with the example script of
`services/echolot.md`: 2 agree (Baroja, Unamuno), 1 conflict (Goya), 14 new, mostly at score 1.0
(Picasso, Gehry, Ravel, …), 3 without a candidate (Chillida, Atxaga, Aurelio Arteta), and
confident wrong matches (Jorge Oteiza → "Oteiza" Q682821 at 1.0, Rafael Ruiz Balerdi → the
footballer Leonardo Balerdi at 0.93). ECHOLOT's answers vary a little between calls: on
2026-10-27 Aurelio Arteta matched the football manager Mikel Arteta.
