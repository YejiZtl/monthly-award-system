# Meeting refinement implementation plan
Goal: implement the eight DOCX changes and expandable independent meeting notes.
Spec: 更新2/20261021月奖考核表需修改的地方.docx; user approved Q zero award.
Architecture: retain SQLite snapshots and audit. Add Q fixed row 19; project rows start at20. Retain internal Excel coordinates and hide weight column for compatibility. Use supplied workbook unchanged as source for downloadable blank templates.
- [ ] Tests: optional reason, locked revisions, Q template/calculation/export, independent project notes.
- [ ] Backend: schemas, repository lock, standard template registry, summary rows/export mapping, per-project remarks.
- [ ] Frontend: remove replacements/weight, visible pinned edit column, expandable notes with append, system sans fonts and larger type.
- [ ] Verify full Python/JS tests and browser, sync public assets, build distinct0.7.1 update package.
