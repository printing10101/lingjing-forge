/**
 * NLInputPanel 拆分后子组件共享类型定义
 */

import type { CADParams } from "@/types/nl2cad";

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  type?: "params" | "model" | "text";
  params?: CADParams;
  modelPath?: string;
  modelName?: string;
  format?: string;
}
