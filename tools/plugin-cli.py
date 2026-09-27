import argparse
import json
import os
import shutil
from datetime import datetime
from pathlib import Path


PLUGIN_TYPES = {
    "adapter": {
        "capabilities": ["data_source", "machine_control"],
        "description": "机床通信协议适配器",
    },
    "data_source": {
        "capabilities": ["data_source"],
        "description": "数据采集源",
    },
    "analyzer": {
        "capabilities": ["data_source"],
        "description": "数据分析处理器",
    },
    "visualization": {
        "capabilities": ["data_source"],
        "description": "数据可视化组件",
    },
}


def create_plugin(name: str, plugin_type: str, author: str, output_dir: str):
    if plugin_type not in PLUGIN_TYPES:
        print(f"Error: Invalid plugin type '{plugin_type}'. Choose from: {', '.join(PLUGIN_TYPES.keys())}")
        return
    
    plugin_id = name.lower().replace(" ", "-").replace("_", "-")
    output_path = Path(output_dir) / plugin_id
    
    if output_path.exists():
        print(f"Error: Plugin directory '{output_path}' already exists")
        return
    
    output_path.mkdir(parents=True)
    
    type_info = PLUGIN_TYPES[plugin_type]
    
    metadata = {
        "id": plugin_id,
        "name": name,
        "version": "1.0.0",
        "author": author,
        "description": type_info["description"],
        "entry_point": "main.py",
        "plugin_type": plugin_type,
        "capabilities": type_info["capabilities"],
        "dependencies": [],
        "config_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "compatibility": {
            "min_core_version": "1.6.0",
            "max_core_version": "2.9.9",
        },
    }
    
    with open(output_path / "plugin.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    main_content = f'''"""
{name} - {type_info["description"]}

Plugin ID: {plugin_id}
Version: 1.0.0
Author: {author}
Created: {datetime.now().strftime("%Y-%m-%d")}
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class Plugin:
    """{name} plugin implementation."""
    
    def __init__(self):
        self.metadata = None
        self.config = {{}}
        self.initialized = False
    
    def set_metadata(self, metadata):
        """Set plugin metadata."""
        self.metadata = metadata
    
    def set_config(self, config: Dict[str, Any]):
        """Set plugin configuration."""
        self.config = config
    
    def initialize(self, context: Dict[str, Any]):
        """Initialize the plugin."""
        logger.info(f"Initializing {{self.metadata.name}} v{{self.metadata.version}}")
        self.initialized = True
    
    def shutdown(self):
        """Shutdown the plugin."""
        logger.info(f"Shutting down {{self.metadata.name}}")
        self.initialized = False
    
    def on_enable(self):
        """Called when plugin is enabled."""
        logger.info(f"Plugin {{self.metadata.name}} enabled")
    
    def on_disable(self):
        """Called when plugin is disabled."""
        logger.info(f"Plugin {{self.metadata.name}} disabled")


def get_plugin_class():
    """Return the plugin class."""
    return Plugin
'''
    
    with open(output_path / "main.py", "w", encoding="utf-8") as f:
        f.write(main_content)
    
    readme_content = f'''# {name}

{type_info["description"]}

## Installation

Place this directory in the plugins folder or use the plugin manager UI.

## Configuration

Add configuration in plugin.json or through the plugin manager UI.

## Capabilities

This plugin requires the following capabilities:
{chr(10).join(f"- {cap}" for cap in type_info["capabilities"])}

## Development

Run in development mode using file:// protocol:
```
python tools/plugin-cli.py dev {plugin_id}
```
'''
    
    with open(output_path / "README.md", "w", encoding="utf-8") as f:
        f.write(readme_content)
    
    print(f"Plugin '{name}' created successfully at: {output_path}")
    print(f"  - ID: {plugin_id}")
    print(f"  - Type: {plugin_type}")
    print(f"  - Version: 1.0.0")
    print(f"  - Capabilities: {', '.join(type_info['capabilities'])}")


def main():
    parser = argparse.ArgumentParser(description="灵境制造插件系统脚手架工具")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    create_parser = subparsers.add_parser("create", help="Create a new plugin")
    create_parser.add_argument("name", help="Plugin name")
    create_parser.add_argument(
        "--type",
        choices=list(PLUGIN_TYPES.keys()),
        default="data_source",
        help="Plugin type",
    )
    create_parser.add_argument("--author", default="灵境制造团队", help="Plugin author")
    create_parser.add_argument("--output", default="plugins", help="Output directory")
    
    args = parser.parse_args()
    
    if args.command == "create":
        create_plugin(args.name, args.type, args.author, args.output)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
