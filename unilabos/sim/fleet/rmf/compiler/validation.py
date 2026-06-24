"""编译期校验（#17 §6.3「不能自动推断的内容」/ #18 §5.1）。

把无法自动推断、必须显式提供的语义缺口转成可读 diagnostics，写入 RmfMapIR。
不抛异常（除非结构性致命），由调用方据 `RmfMapIR.has_errors()` 决定是否拒绝编译。
"""

from __future__ import annotations

from unilabos.sim.fleet.rmf.compiler.rmf_ir import RmfDiagnostic, RmfMapIR


def validate_ir(ir: RmfMapIR) -> RmfMapIR:
    """对 IR 做完整性校验，把问题 append 进 ir.diagnostics 并返回同一对象。"""
    diags = ir.diagnostics

    if not ir.levels:
        diags.append(RmfDiagnostic("error", "no_levels", "编译结果不含任何 level"))
        return ir

    # 收集所有 charger waypoint 名
    charger_names = set()
    waypoint_names = set()
    for level in ir.levels:
        for v in level.vertices:
            waypoint_names.add(v.name)
            if v.params.get("is_charger"):
                charger_names.add(v.name)

    if not waypoint_names:
        diags.append(RmfDiagnostic("error", "no_waypoints", "没有任何命名 waypoint，AGV 无法寻址"))

    # 机器人级校验
    for robot in ir.robots:
        if not robot.charger_waypoint:
            diags.append(
                RmfDiagnostic("error", "missing_charger", f"机器人 {robot.robot_name} 未指定 charger_waypoint", robot.robot_name)
            )
        elif robot.charger_waypoint not in charger_names:
            diags.append(
                RmfDiagnostic(
                    "error",
                    "charger_not_found",
                    f"机器人 {robot.robot_name} 的 charger '{robot.charger_waypoint}' 不是已编译的 is_charger waypoint",
                    robot.robot_name,
                )
            )
        if robot.kind == "real" and not robot.target_map:
            diags.append(
                RmfDiagnostic(
                    "error",
                    "missing_target_map",
                    f"真实机器人 {robot.robot_name} 缺少 target_map（waypoint→SEER id），将禁止 dispatch",
                    robot.robot_name,
                )
            )
        if robot.initial_waypoint and robot.initial_waypoint not in waypoint_names:
            diags.append(
                RmfDiagnostic(
                    "warning",
                    "initial_waypoint_not_found",
                    f"机器人 {robot.robot_name} 的 initial_waypoint '{robot.initial_waypoint}' 不在 waypoint 列表",
                    robot.robot_name,
                )
            )

    # lane 连通性（最弱校验：至少有一条 lane）
    total_lanes = sum(len(level.lanes) for level in ir.levels)
    if total_lanes == 0:
        diags.append(
            RmfDiagnostic("warning", "no_lanes", "没有可通行 lane，nav_graph 将为空（第一版需人工 RMF navigation layer）")
        )

    return ir
