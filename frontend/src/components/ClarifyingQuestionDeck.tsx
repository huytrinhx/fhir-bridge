import { useState } from "react";
import type { ClarifyingQuestionItem } from "../types";

interface Props {
  questions: ClarifyingQuestionItem[];
  // combinedAnswer is what resumes the graph (one Q/A block per question,
  // readable by the model); displaySummary is the friendlier line appended
  // to the chat thread as the "user" bubble for this turn.
  onSubmit: (combinedAnswer: string, displaySummary: string) => void;
}

export default function ClarifyingQuestionDeck({ questions, onSubmit }: Props) {
  const [cardIndex, setCardIndex] = useState(0);
  const [answers, setAnswers] = useState<string[]>(() => questions.map(() => ""));

  const current = questions[cardIndex];
  const answer = answers[cardIndex];
  const isLast = cardIndex === questions.length - 1;
  const canAdvance = answer.trim().length > 0;

  function setAnswer(value: string) {
    setAnswers((prev) => {
      const next = [...prev];
      next[cardIndex] = value;
      return next;
    });
  }

  function handleSubmit() {
    const combined = questions
      .map((q, i) => `Q${i + 1}: ${q.question}\nA${i + 1}: ${answers[i]}`)
      .join("\n\n");
    const displaySummary =
      questions.length === 1 ? answers[0] : questions.map((_, i) => `${i + 1}. ${answers[i]}`).join("  ");
    onSubmit(combined, displaySummary);
  }

  return (
    <div className="clarify-deck">
      {questions.length > 1 && (
        <div className="clarify-deck__progress">
          Question {cardIndex + 1} of {questions.length}
        </div>
      )}
      <div className="clarify-deck__question">{current.question}</div>

      {current.options && (
        <div className="clarify-deck__options">
          {current.options.map((option) => (
            <button
              key={option}
              type="button"
              className={
                "clarify-deck__option" + (answer === option ? " clarify-deck__option--selected" : "")
              }
              onClick={() => setAnswer(option)}
            >
              {option}
            </button>
          ))}
        </div>
      )}

      <input
        type="text"
        className="clarify-deck__input"
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
        placeholder={current.options ? "Or type your own answer…" : "Type your answer…"}
      />

      <div className="clarify-deck__nav">
        <button
          type="button"
          className="secondary-button"
          onClick={() => setCardIndex((i) => i - 1)}
          disabled={cardIndex === 0}
        >
          ← Back
        </button>
        {isLast ? (
          <button type="button" className="clarify-deck__submit" onClick={handleSubmit} disabled={!canAdvance}>
            Submit answers
          </button>
        ) : (
          <button
            type="button"
            className="clarify-deck__submit"
            onClick={() => setCardIndex((i) => i + 1)}
            disabled={!canAdvance}
          >
            Next →
          </button>
        )}
      </div>
    </div>
  );
}
