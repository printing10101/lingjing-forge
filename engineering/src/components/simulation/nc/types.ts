export interface SimParams {
  voxelSize: number;
  toolType: string;
  toolDiameter: number;
  toolLength: number;
  toolCornerRadius: number;
  safeZ: number;
  stockStlPath: string;
}

export interface HistoryItem {
  task_id: string;
  project_id: string;
  duration_seconds: number;
  collision_collided: boolean;
  voxel_size: number;
  segment_count: number;
}

export interface SimResultData {
  task_id: string;
  collision_detected: boolean;
  collision_details: {
    timestamp: string;
    positions: number[][];
    segment_indices: number[];
    severity: string;
    count: number;
  } | null;
  duration_seconds: number;
  voxel_count: number;
  removed_voxel_count: number;
  voxel_size: number;
  toolpath_segment_count: number;
  simulation_result: {
    workpiece_stl_path: string;
    voxel_count: number;
    removed_voxel_count: number;
    voxel_size: number;
    original_bbox: Record<string, number> | null;
  } | null;
}

export type SimState = "idle" | "running" | "completed" | "failed";
