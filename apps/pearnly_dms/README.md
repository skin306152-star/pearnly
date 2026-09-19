### Pearnly DMS

DMS 产品：身份证录入、客户匹配、操作手册、推送记录

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch main
bench install-app pearnly_dms
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/pearnly_dms
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit

## 当前模块入口

业务实现已按域归位，见 [架构与职责](../../docs/development/architecture.md) 及 [旧新路径映射](../../docs/development/dms-module-map.json)。根层的旧 API/队列模块只保留兼容导出，不再承载业务；历史 P4 文档中的路径需通过映射表定位。
