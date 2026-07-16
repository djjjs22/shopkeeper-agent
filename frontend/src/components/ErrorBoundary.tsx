/**
 * React 错误边界
 * 捕获子组件树渲染期间的错误，显示降级 UI 防止白屏
 * 必须用 class 组件（Hooks API 暂不支持 getDerivedStateFromError）
 */
import { AlertTriangle, RotateCcw } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";

type ErrorBoundaryProps = {
  children: ReactNode;
  /** 可选的降级 UI 渲染函数，默认用内置的 fallback */
  fallback?: (error: Error, reset: () => void) => ReactNode;
};

type ErrorBoundaryState = {
  error: Error | null;
};

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 真实生产可上传到 Sentry / 自建错误监控
    // 这里只打到 console
    console.error("[ErrorBoundary] 捕获到错误：", error, info.componentStack);
  }

  reset = () => {
    this.setState({ error: null });
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) {
      return this.props.fallback(error, this.reset);
    }

    return <DefaultFallback error={error} reset={this.reset} />;
  }
}

function DefaultFallback({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div
      role="alert"
      className="mx-auto my-12 flex max-w-md flex-col items-center gap-4 border border-tomato/35 bg-tomato/5 px-6 py-8 text-center"
    >
      <div className="grid h-12 w-12 place-items-center rounded-full bg-tomato/15 text-tomato">
        <AlertTriangle className="h-6 w-6" aria-hidden="true" />
      </div>
      <div className="space-y-1">
        <p className="text-base font-semibold text-ink">页面出错了</p>
        <p className="text-sm text-ink/55">{error.message || "未知错误"}</p>
      </div>
      <button
        type="button"
        onClick={reset}
        className="inline-flex items-center gap-2 border border-ink/20 bg-white px-4 py-2 text-sm font-medium text-ink transition hover:bg-ink hover:text-parchment focus:outline-none focus:ring-2 focus:ring-moss/40"
      >
        <RotateCcw className="h-4 w-4" aria-hidden="true" />
        重试
      </button>
    </div>
  );
}