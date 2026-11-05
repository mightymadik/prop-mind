import { Component, type ReactNode } from "react";
import { CircleAlert } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

// Ошибка отрисовки не должна оставлять пустой экран: показываем сообщение и даём начать заново.
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error("[prop-mind] render error", error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="mx-auto my-10 max-w-[900px] px-4">
        <Alert variant="destructive" className="border-destructive/50">
          <CircleAlert />
          <AlertDescription>
            Что-то пошло не так при показе результата.
            <Button variant="link" className="h-auto p-0 text-primary" onClick={() => location.reload()}>
              Начать заново
            </Button>
          </AlertDescription>
        </Alert>
      </main>
    );
  }
}
