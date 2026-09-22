# Milestone 7: class presentation

Class details use a dedicated production presentation. The detail view contains
class metadata, starting equipment, multiclassing text, spellcasting ability,
the persisted progression table, class features grouped by level, and separate
subclass sections with their own level-grouped features.

The progression table is built from persisted column and value records. It does
not assume class-specific resources. A declared proficiency-bonus column is
shown once; when it is absent, the stored level proficiency value is shown in a
dedicated `PB` column.

At narrow widths the table remains horizontally scrollable. Focus it and use
Left/Right or `h`/`l` to move horizontally. Up/Down, `j`/`k`, and PageUp/PageDown
navigate table rows. The surrounding detail remains a normal scrollable view;
at 50–79 columns, Enter opens it and Escape returns to results.
