import { useEffect, useLayoutEffect, useReducer, useRef, useState } from "react";
import { AssistantMessage } from "./components/AssistantMessage";
import { Composer, type ComposerHandle } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { ArrowDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Logo } from "./components/Logo";
import { searchOnce, searchStream, SearchError } from "./lib/api";
import type { Params } from "./params";
import { chatReducer, emptyChat } from "./state";

const NEAR_BOTTOM_PX = 64;
const COLUMN = "mx-auto w-full max-w-[900px] px-4 max-sm:px-3"; // общая колонка: одна левая линия у шапки, ленты и поля
let seq = 0;
const nextId = () => `t${++seq}`;

export function App({ params }: { params: Params }) {
  const [chat, dispatch] = useReducer(chatReducer, emptyChat);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);
  const log = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true); // пользователь внизу ленты — тогда новое прокручиваем само
  const lastTop = useRef(0);
  const cardRefs = useRef(new Map<string, HTMLDivElement>());
  const composer = useRef<ComposerHandle>(null);
  const busy = chat.turns.some((t) => t.reply.busy);

  function toBottom(smooth = false) {
    const el = log.current;
    if (!el) return;
    if (smooth && el.scrollTo) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    else el.scrollTop = el.scrollHeight;
    lastTop.current = el.scrollTop;
    atBottom.current = true;
    setShowNew(false);
  }

  // после каждого обновления ленты: внизу — докручиваем; отмотал вверх — показываем «К новым»
  useLayoutEffect(() => {
    if (!chat.turns.length) return;
    if (atBottom.current) toBottom();
    else setShowNew(true);
  }, [chat]);

  useEffect(() => {
    if (!highlighted) return;
    const t = setTimeout(() => setHighlighted(null), 2000);
    return () => clearTimeout(t);
  }, [highlighted]);

  async function ask(query: string) {
    const id = nextId();
    atBottom.current = true; // свой запрос — всегда показываем
    dispatch({ type: "ask", id, query, stream: params.stream });
    try {
      if (params.stream) {
        await searchStream(query, params.siteKey, (event) => dispatch({ type: "event", id, event }));
        dispatch({ type: "end", id }); // без done ход не должен остаться «в процессе» навсегда
      } else {
        dispatch({ type: "payload", id, payload: await searchOnce(query, params.siteKey) });
      }
    } catch (e) {
      dispatch({ type: "fail", id, text: e instanceof SearchError ? e.message : "Не получилось выполнить поиск. Попробуйте ещё раз." });
    }
    composer.current?.focus();
  }

  function showCard(domId: string) {
    const card = cardRefs.current.get(domId);
    if (!card) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    card.scrollIntoView?.({ behavior: reduce ? "auto" : "smooth", block: "nearest", inline: "center" });
    card.focus({ preventScroll: true });
    setHighlighted(domId);
  }

  function selectListing(sourceId: string) {
    // в виджете сайт сам открывает карточку объекта
    if (params.embed && params.hostOrigin && window.parent !== window) {
      window.parent.postMessage({ type: "prop-mind:listing", source_id: sourceId }, params.hostOrigin);
    }
  }

  return (
    <div className="flex h-dvh flex-col">
      {!params.embed && (
        <header className="topbar border-b bg-card">
          <div className={`${COLUMN} flex items-center justify-between gap-3 py-3`}>
            <Logo />
            <Badge variant="outline" className="font-normal text-muted-foreground">
              Demo
            </Badge>
          </div>
        </header>
      )}

      <div
        className="flex-1 overflow-y-auto overscroll-contain [overflow-anchor:none]" /* прокруткой управляет App */
        ref={log}
        role="log"
        aria-label="Переписка"
        onScroll={(e) => {
          const el = e.currentTarget;
          const near = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
          // «ушёл читать вверх» — только если прокрутка реально уменьшилась: рост контента и
          // подстройки браузера дают scroll-событие с отступом снизу, но не движение вверх
          if (near) atBottom.current = true;
          else if (el.scrollTop < lastTop.current) atBottom.current = false;
          lastTop.current = el.scrollTop;
          if (atBottom.current) setShowNew(false);
        }}
      >
        <div className={`${COLUMN} flex flex-col gap-7 pt-6 pb-3 max-sm:gap-5 max-sm:pt-4`}>
          {chat.turns.length === 0 ? (
            <EmptyState onPick={ask} disabled={busy} />
          ) : (
            chat.turns.map((t) => (
              <section key={t.id} className="flex flex-col gap-3" aria-label={`Запрос: ${t.query}`}>
                <div className="msg user flex justify-end">
                  <div className="max-w-[min(80%,560px)] rounded-lg bg-primary px-3.5 py-2 text-primary-foreground [overflow-wrap:anywhere] whitespace-pre-wrap max-sm:max-w-[88%]">
                    {t.query}
                  </div>
                </div>
                <AssistantMessage
                  turnId={t.id}
                  reply={t.reply}
                  highlighted={highlighted}
                  cardRefs={cardRefs}
                  onRef={showCard}
                  onSelect={selectListing}
                />
              </section>
            ))
          )}
        </div>
      </div>

      <div className="relative border-t bg-background pt-2.5 pb-[calc(12px+env(safe-area-inset-bottom))]">
        {showNew && (
          <Button
            variant="secondary"
            size="sm"
            className="absolute -top-12 left-1/2 -translate-x-1/2 shadow-md"
            onClick={() => toBottom(true)}
          >
            К новым <ArrowDown />
          </Button>
        )}
        <div className={COLUMN}>
          <Composer ref={composer} busy={busy} onSend={ask} />
        </div>
      </div>
    </div>
  );
}
