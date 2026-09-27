-- TDengine 初始化脚本
-- 用于创建灵境制造系统所需的数据库和表结构
-- 执行方式: taos -h localhost -u <user> -p<password> < init.sql
-- 示例: taos -h localhost -u root -p$(echo $TDENGINE_PASSWORD) < init.sql

-- ============================================================
-- 1. 创建数据库
-- ============================================================
CREATE DATABASE IF NOT EXISTS lnn_tsdb
    PRECISION 'ms'
    KEEP 365
    DAYS 30
    UPDATE 1;

USE lnn_tsdb;

-- ============================================================
-- 2. 创建超级表 (Super Tables)
-- ============================================================

-- 2.1 OPC UA 数据超级表 - 用于存储机床传感器数据
CREATE STABLE IF NOT EXISTS opcua_data (
    ts TIMESTAMP,
    spindle_speed DOUBLE,
    spindle_load DOUBLE,
    feedrate DOUBLE,
    execution BINARY(32),
    vibration_x DOUBLE,
    vibration_y DOUBLE,
    vibration_z DOUBLE,
    temperature DOUBLE,
    current DOUBLE,
    voltage DOUBLE
) TAGS (
    machine_id BINARY(64),
    work_order_no BINARY(64),
    product_code BINARY(64)
);

-- 2.2 振动数据超级表 - 用于高频振动传感器数据
CREATE STABLE IF NOT EXISTS vibration_data (
    ts TIMESTAMP,
    accel_x DOUBLE,
    accel_y DOUBLE,
    accel_z DOUBLE,
    velocity_x DOUBLE,
    velocity_y DOUBLE,
    velocity_z DOUBLE,
    displacement DOUBLE,
    frequency DOUBLE,
    rms_value DOUBLE
) TAGS (
    machine_id BINARY(64),
    sensor_id BINARY(64),
    measurement_point BINARY(64)
);

-- 2.3 工艺参数超级表 - 用于存储加工工艺参数
CREATE STABLE IF NOT EXISTS process_params (
    ts TIMESTAMP,
    spindle_speed DOUBLE,
    feedrate DOUBLE,
    depth_of_cut DOUBLE,
    width_of_cut DOUBLE,
    tool_id BINARY(32),
    material_code BINARY(32),
    coolant_flow DOUBLE,
    coolant_temp DOUBLE
) TAGS (
    machine_id BINARY(64),
    work_order_no BINARY(64),
    operation_type BINARY(32)
);

-- 2.4 质量数据超级表 - 用于存储质量检测数据
CREATE STABLE IF NOT EXISTS quality_data (
    ts TIMESTAMP,
    dimension_x DOUBLE,
    dimension_y DOUBLE,
    dimension_z DOUBLE,
    surface_roughness DOUBLE,
    roundness DOUBLE,
    cylindricity DOUBLE,
    tolerance DOUBLE,
    inspection_result BINARY(16)
) TAGS (
    machine_id BINARY(64),
    work_order_no BINARY(64),
    batch_no BINARY(64),
    inspection_type BINARY(32)
);

-- 2.5 设备状态超级表 - 用于存储设备运行状态
CREATE STABLE IF NOT EXISTS equipment_status (
    ts TIMESTAMP,
    status BINARY(32),
    mode BINARY(32),
    alarm_code BINARY(64),
    alarm_message BINARY(256),
    uptime DOUBLE,
    downtime DOUBLE,
    oee DOUBLE
) TAGS (
    machine_id BINARY(64),
    line_id BINARY(64),
    workshop_id BINARY(64)
);

-- 2.6 能耗数据超级表 - 用于存储设备能耗数据
CREATE STABLE IF NOT EXISTS energy_consumption (
    ts TIMESTAMP,
    active_power DOUBLE,
    reactive_power DOUBLE,
    apparent_power DOUBLE,
    power_factor DOUBLE,
    energy_kwh DOUBLE,
    current_a DOUBLE,
    current_b DOUBLE,
    current_c DOUBLE,
    voltage_ab DOUBLE,
    voltage_bc DOUBLE,
    voltage_ca DOUBLE
) TAGS (
    machine_id BINARY(64),
    meter_id BINARY(64)
);

-- 2.7 刀具寿命超级表 - 用于存储刀具使用数据
CREATE STABLE IF NOT EXISTS tool_life (
    ts TIMESTAMP,
    tool_id BINARY(32),
    tool_type BINARY(32),
    cutting_time DOUBLE,
    cutting_length DOUBLE,
    wear_value DOUBLE,
    remaining_life DOUBLE,
    replacement_flag BOOL
) TAGS (
    machine_id BINARY(64),
    tool_holder_id BINARY(32)
);

-- ============================================================
-- 3. 创建普通表 (用于非时序数据)
-- ============================================================

-- 3.1 工单执行记录表
CREATE TABLE IF NOT EXISTS work_order_execution (
    ts TIMESTAMP,
    work_order_no BINARY(64),
    product_code BINARY(64),
    quantity_planned INT,
    quantity_completed INT,
    quantity_defective INT,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    status BINARY(32),
    operator_id BINARY(64),
    machine_id BINARY(64)
);

-- 3.2 维护保养记录表
CREATE TABLE IF NOT EXISTS maintenance_log (
    ts TIMESTAMP,
    machine_id BINARY(64),
    maintenance_type BINARY(32),
    description BINARY(512),
    parts_replaced BINARY(256),
    cost DOUBLE,
    next_maintenance_date TIMESTAMP,
    performed_by BINARY(64)
);

-- ============================================================
-- 4. 创建用户和权限 (可选)
-- ============================================================
-- 创建应用专用用户（生产环境建议）
-- CREATE USER IF NOT EXISTS lnn_app PASS 'your_secure_password';
-- GRANT ALL ON lnn_tsdb TO lnn_app;

-- ============================================================
-- 5. 验证创建结果
-- ============================================================
SHOW DATABASES;
SHOW STABLES;
SHOW TABLES;

-- 完成提示
SELECT 'TDengine initialization completed successfully!' AS message;
