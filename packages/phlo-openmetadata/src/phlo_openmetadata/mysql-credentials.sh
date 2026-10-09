# Sourced by the MySQL entrypoint after the image's mysql-script.sql has
# created its built-in users with published passwords. Replaces the
# OpenMetadata user's password with the generated one and drops the unused
# Airflow user, so the datastore never accepts repository-known credentials.
_phlo_sql_quote() {
    local value=${1//\\/\\\\}
    printf '%s' "${value//\'/\'\'}"
}
"${mysql[@]}" <<SQL
ALTER USER 'openmetadata_user'@'%' IDENTIFIED BY '$(_phlo_sql_quote "${OPENMETADATA_DB_PASSWORD:?OPENMETADATA_DB_PASSWORD is required}")';
DROP USER IF EXISTS 'airflow_user'@'%';
FLUSH PRIVILEGES;
SQL
