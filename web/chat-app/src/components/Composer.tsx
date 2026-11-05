import { forwardRef, useImperativeHandle, useRef, useState } from "react";
import { CornerDownLeft, LoaderCircle, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

const MAX = 500; // = QueryIn.query max_length на сервере
const WARN_AT = 400; // счётчик показываем только у лимита

interface Props {
  busy: boolean;
  onSend: (query: string) => void;
}

export interface ComposerHandle {
  focus: () => void;
}

function Kbd({ children }: { children: string }) {
  return (
    <kbd className="rounded border border-b-2 border-border bg-background px-1.5 py-px font-sans text-[11px] font-medium text-foreground">
      {children}
    </kbd>
  );
}

export const Composer = forwardRef<ComposerHandle, Props>(function Composer({ busy, onSend }, ref) {
  const [query, setQuery] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(ref, () => ({ focus: () => input.current?.focus() }));

  function fit() {
    // поле растёт с текстом (до max-h); высота через CSSOM — CSP это разрешает.
    // field-sizing: content не везде поддерживается, поэтому считаем сами
    const el = input.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }

  function send() {
    const q = query.trim();
    if (!q || busy) return;
    onSend(q);
    setQuery("");
    requestAnimationFrame(fit);
  }

  return (
    <form
      className="w-full"
      autoComplete="off"
      onSubmit={(e) => {
        e.preventDefault();
        send();
      }}
    >
      <div
        className="group rounded-3xl border border-input bg-card shadow-[0_1px_2px_rgb(15_23_42/0.04),0_10px_28px_-14px_rgb(30_64_175/0.35)] transition-[box-shadow,border-color] focus-within:border-primary focus-within:ring-4 focus-within:ring-primary/15"
        onClick={(e) => {
          // клик по пустому месту блока — фокус в поле, как у обычного поля ввода
          if (e.target === e.currentTarget) input.current?.focus();
        }}
      >
        <div className="flex items-start gap-3 px-5 pt-3">
          <Search
            aria-hidden="true"
            className="mt-2.5 size-5 flex-none text-muted-foreground transition-colors group-focus-within:text-primary"
          />
          <label className="sr-only" htmlFor="q">
            Запрос
          </label>
          <Textarea
            id="q"
            ref={input}
            rows={1}
            maxLength={MAX}
            value={query}
            placeholder="Опишите квартиру…"
            className="field-sizing-fixed max-h-40 min-h-10 resize-none rounded-none border-0 bg-transparent px-0 py-2 text-base shadow-none focus-visible:ring-0 md:text-base"
            onChange={(e) => {
              setQuery(e.target.value);
              fit();
            }}
            onKeyDown={(e) => {
              // Enter — отправить, Shift+Enter — новая строка; во время набора через IME — не отправлять
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
          />
        </div>
        <div className="flex items-center justify-between gap-3 py-2 pr-2 pl-5">
          <p className="flex items-center gap-1 text-xs text-muted-foreground max-sm:hidden">
            <Kbd>Enter</Kbd> найти · <Kbd>Shift</Kbd>+<Kbd>Enter</Kbd> новая строка
          </p>
          <div className="ml-auto flex items-center gap-3">
            {query.length >= WARN_AT && (
              <span className="text-xs text-muted-foreground tabular-nums" aria-live="polite">
                {`${query.length} / ${MAX}`}
              </span>
            )}
            <Button type="submit" disabled={busy || !query.trim()} className="h-10 min-w-28 rounded-full px-5 font-semibold">
              {busy ? (
                <>
                  <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> Ищу…
                </>
              ) : (
                <>
                  Найти <CornerDownLeft aria-hidden="true" />
                </>
              )}
            </Button>
          </div>
        </div>
      </div>
    </form>
  );
});
