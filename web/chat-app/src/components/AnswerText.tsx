import { segmentAnswer } from "../lib/refs";
import type { Listing } from "../types";

interface Props {
  text: string;
  results: Listing[];
  onRef: (sourceId: string) => void;
}

// Текст модели — только текстом; ссылки «№ n» — отдельные элементы.
export function AnswerText({ text, results, onRef }: Props) {
  if (!text) return null;
  return (
    <div className="answer-text max-w-[70ch] text-[1.02rem] leading-relaxed [overflow-wrap:anywhere] whitespace-pre-wrap">
      {segmentAnswer(text, results).map((s, i) =>
        s.kind === "text" ? (
          <span key={i}>{s.text}</span>
        ) : (
          <a
            key={i}
            className="ref font-semibold whitespace-nowrap text-primary underline decoration-1 underline-offset-[3px] hover:decoration-2"
            href={`#${s.sourceId}`}
            onClick={(e) => {
              e.preventDefault();
              onRef(s.sourceId);
            }}
          >
            № {s.index + 1}
          </a>
        ),
      )}
    </div>
  );
}
