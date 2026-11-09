# CLSEN产品知识库公开CI入口

本仓库存放CI配置和执行控制器，主源码位于私有仓库dulanjy/CLSEN_Rag。

## 执行

1. 打开Actions中的Product Knowledge CI，从main手动运行，填写私有源码的完整40位提交SHA。
2. 所有者审批private-source-ci环境后，独立检出步骤使用专属只读部署密钥检出该确切提交。
3. 执行锁定依赖、完整前端、核心后端、隔离PostgreSQL及Linux镜像检查，公开日志仅包含阶段结果、提交身份及测试计数。

完整输出、源码、前端构建和镜像保存在临时私有工作区，任务结束清理。配置自检在本仓库push/PR时运行。指定源码需包含产品知识库当前CI锁文件、workflow和可移植测试入口。

## 凭证及发布

环境private-source-ci中的CLSEN_SOURCE_READ_KEY仅能读取dulanjy/CLSEN_Rag。密钥只供应独立检出步骤，步骤结束后由新的无凭证进程执行测试。测试环境使用明确允许的系统/工具变量，公开summary和runner命令文件变量在该边界外。

手动镜像发布位于主源码私有仓库，关联本仓库同一源码SHA的成功CI记录，再完成私有发布校验。主源码提交与CI仓库提交分别记录。
