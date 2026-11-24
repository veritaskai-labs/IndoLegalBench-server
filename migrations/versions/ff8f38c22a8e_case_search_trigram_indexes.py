"""Index trigram untuk pencarian daftar kasus. PBI-4 SCRUM-81 SCRUM-339, AC3.

GET /cases mencari kata kunci di tengah kode kasus atau judul ("%kata%"). Index btree tidak
bisa membantu pola yang diawali %, jadi tanpa index ini setiap pencarian membaca dan
mengecilkan semua baris cases dan case_versions. pg_trgm memecah teks menjadi potongan tiga
huruf, sehingga index GIN bisa menemukan baris yang memuat potongan kata kunci.

Ekspresi index sama persis dengan yang dipakai query (lower(case_code) dan
lower(content->>'title')); kalau berbeda, planner tidak memakainya. Diukur di PostgreSQL 16
dengan 10.000 kasus (deskripsi PR SCRUM-339): COUNT kata kunci judul 12,5 -> 6,3 ms, kata kunci kode
14,6 -> 2,8 ms.

pg_trgm termasuk extension "trusted" sejak PostgreSQL 13, jadi pemilik database boleh
memasangnya tanpa superuser. downgrade() hanya menghapus index; extension dibiarkan, karena
bisa saja sudah dipakai objek lain dan tidak berbahaya kalau tetap ada.
"""

from alembic import op

revision = "ff8f38c22a8e"  # pragma: allowlist secret
down_revision = "f2a7c4e91b38"  # pragma: allowlist secret
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_cases_case_code_trgm ON cases USING gin (lower(case_code) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_case_versions_title_trgm ON case_versions "
        "USING gin (lower(content ->> 'title') gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_case_versions_title_trgm")
    op.execute("DROP INDEX IF EXISTS ix_cases_case_code_trgm")
