"""Compatibility exports for the foundational project configuration loader."""

from phlo.config.project import (
    ProjectConfigError as ProjectConfigError,
)
from phlo.config.project import (
    _default_project_root as _default_project_root,
)
from phlo.config.project import (
    clear_config_cache as clear_config_cache,
)
from phlo.config.project import (
    get_api_authorization_config as get_api_authorization_config,
)
from phlo.config.project import (
    get_authentication_config as get_authentication_config,
)
from phlo.config.project import (
    get_authentication_provider_config as get_authentication_provider_config,
)
from phlo.config.project import (
    get_capability_defaults_from_config as get_capability_defaults_from_config,
)
from phlo.config.project import (
    get_configured_authentication_provider_name as get_configured_authentication_provider_name,
)
from phlo.config.project import (
    get_configured_authorization_backend_name as get_configured_authorization_backend_name,
)
from phlo.config.project import (
    get_container_name as get_container_name,
)
from phlo.config.project import (
    get_project_name_from_config as get_project_name_from_config,
)
from phlo.config.project import (
    get_regulated_config as get_regulated_config,
)
from phlo.config.project import (
    get_regulated_mode_config as get_regulated_mode_config,
)
from phlo.config.project import (
    get_service_config as get_service_config,
)
from phlo.config.project import (
    load_infrastructure_config as load_infrastructure_config,
)
from phlo.config.project import (
    load_project_config as load_project_config,
)
from phlo.config.project import (
    load_project_model as load_project_model,
)
from phlo.config.project import (
    load_wap_config as load_wap_config,
)
