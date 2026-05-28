import { notification } from "antd";

// Global top-right toast popups. Used for errors that would otherwise be cramped
// into inline status text.
export function notifyError(detail: string, title = "操作失败"): void {
  notification.error({
    // Dedupe identical messages (e.g. repeated polling failures) into one toast
    // instead of stacking endlessly.
    key: `api-error:${detail}`,
    message: title,
    description: detail,
    placement: "topRight",
    duration: 5
  });
}

export function notifySuccess(detail: string, title = "操作成功"): void {
  notification.success({
    message: title,
    description: detail,
    placement: "topRight",
    duration: 3
  });
}
