import json
import os
import sys
from pathlib import Path

if len(sys.argv) > 1:
    path = Path(sys.argv[1])
else:
    path = Path(
        os.environ.get(
            "ASTRBOT_CMD_CONFIG",
            "/opt/astrbot/data/cmd_config.json",
        )
    )
config = json.loads(path.read_text(encoding="utf-8-sig"))
platform_settings = config.setdefault("platform_settings", {})
segmented = platform_settings.setdefault("segmented_reply", {})
segmented.update(
    {
        "enable": True,
        "only_llm_result": True,
        "interval_method": "random",
        "interval": "0.8,1.8",
        "words_count_threshold": 100000,
        "split_mode": "regex",
        "regex": ".*?。|.+$",
        "content_cleanup_rule": "",
    }
)
path.write_text(
    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(
    json.dumps(
        platform_settings["segmented_reply"], ensure_ascii=False, indent=2
    )
)
