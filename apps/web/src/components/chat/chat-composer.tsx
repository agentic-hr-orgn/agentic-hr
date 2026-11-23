"use client";

import React from "react";
import { SendHorizontal, Square } from "lucide-react";
import { Button } from "@/components/ui/button";

const STARTER_PROMPTS: Array<{ label: string; prompt: string }> = [
  {
    label: "Annual Leave Policy",
    prompt: "How many paid annual leave days do full-time employees receive per year?",
  },
  {
    label: "My Leave Balance",
    prompt: "What is my leave balance for this year?",
  },
  {
    label: "Request Leave",
    prompt: "I want to apply for annual leave next Monday",
  },
  {
    label: "بیماری کی چھٹی (Urdu)",
    prompt: "بیماری کی کتنی چھٹیاں ملتی ہیں اور میڈیکل سرٹیفکیٹ کب ضروری ہے؟",
  },
];

interface ChatComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  onAbort?: () => void;
  isStreaming: boolean;
  isBusy: boolean;
  isOffline: boolean;
}

export function ChatComposer({
  value,
  onChange,
  onSend,
  onAbort,
  isStreaming,
  isBusy,
  isOffline,
}: ChatComposerProps) {
  const canSend = !isStreaming && !isBusy && !isOffline && value.trim().length > 0;

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (canSend) {
        onSend();
      }
    }
  };

  return (
    <div className="border-t border-appBorder bg-[#F8F9FD] p-3.5 space-y-2.5">
      <div className="flex flex-wrap gap-1.5">
        {STARTER_PROMPTS.map((item) => (
          <button
            key={item.label}
            type="button"
            disabled={isStreaming || isOffline}
            onClick={() => onChange(item.prompt)}
            className="rounded-full border border-appBorder bg-white px-2.5 py-1 text-[11px] font-medium text-[#102C54] hover:border-[#2A5BDB] hover:text-[#2A5BDB] transition-colors disabled:opacity-50"
          >
            {item.label}
          </button>
        ))}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (canSend) onSend();
        }}
        className="flex flex-col sm:flex-row gap-2 items-stretch sm:items-end"
      >
        <textarea
          dir="auto"
          rows={2}
          aria-label="Ask Policy Brain a question"
          placeholder="Ask about HR policies in English, اردو, or Roman Urdu... (Enter to send, Shift+Enter for newline)"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isOffline}
          className="flex-1 resize-none rounded-medium border border-appBorder bg-white px-3 py-2 text-xs text-[#102C54] placeholder:text-textMuted focus:border-[#2A5BDB] focus:outline-none disabled:bg-slate-50"
        />

        {isStreaming ? (
          <Button
            type="button"
            onClick={onAbort}
            variant="outline"
            className="h-10 px-4 text-xs border-rose-300 text-rose-700 hover:bg-rose-50 inline-flex items-center gap-1.5 shrink-0"
          >
            <Square className="h-3.5 w-3.5" aria-hidden="true" />
            Stop
          </Button>
        ) : (
          <Button
            type="submit"
            disabled={!canSend}
            className="h-10 px-4 text-xs bg-[#2A5BDB] text-white hover:bg-[#102C54] inline-flex items-center gap-1.5 shrink-0"
          >
            <SendHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
            Send
          </Button>
        )}
      </form>
    </div>
  );
}
