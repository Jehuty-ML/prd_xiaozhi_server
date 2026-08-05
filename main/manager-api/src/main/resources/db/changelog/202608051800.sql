-- 默认 max_connections：500 → 200（更贴合单核/中小规格）

UPDATE `sys_params`
SET `param_value`='200',
    `remark`='单进程最大并发 WebSocket 连接数（默认 200；可按机器规格上调）'
WHERE `param_code`='server.connection.max_connections';
