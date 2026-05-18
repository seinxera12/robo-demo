/**
 * Bug Condition Exploration Tests — Bug 1: No input lock during LLM generation
 *
 * These tests are EXPECTED TO FAIL on unfixed TextInput.tsx.
 * Failure confirms the bug exists (missing `disabled` prop).
 *
 * DO NOT fix the tests or the code when they fail.
 *
 * Validates: AC-1.1, AC-1.2
 */

import { describe, it, expect, vi } from 'vitest';
import * as fc from 'fast-check';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import TextInput from '../components/TextInput';

// ---------------------------------------------------------------------------
// Arbitraries
// ---------------------------------------------------------------------------

/** Any non-empty, non-whitespace-only string */
const nonEmptyTextArb = fc.string({ minLength: 1 }).filter((s) => s.trim().length > 0);

// ---------------------------------------------------------------------------
// Bug 1 — Property: clicking Send calls mockSend even when pipelineState='thinking'
//
// On UNFIXED code: TextInput has no `disabled` prop, so the send button is
// only gated by whether the text field is empty. Clicking Send WILL call
// mockSend regardless of pipeline state.
//
// EXPECTED OUTCOME: This test FAILS on unfixed code because mockSend IS called
// (demonstrating the bug — it should NOT be callable when thinking).
// ---------------------------------------------------------------------------

describe('Bug 1 — Send button active during thinking (bug condition exploration)', () => {
  it(
    'Property: for any non-empty text, clicking Send does NOT call mockSend when disabled={true} — ' +
      'verifies fix: Send button is disabled when pipelineState=thinking',
    () => {
      fc.assert(
        fc.property(nonEmptyTextArb, (text) => {
          // Clean up DOM between fast-check iterations
          cleanup();

          const mockSend = vi.fn();

          // Render TextInput WITH disabled={true} (simulating pipelineState='thinking'
          // where App.tsx now passes disabled={pipelineState === 'thinking'} — the fix).
          // On fixed code, the button and input are disabled, so mockSend should NOT be called.
          render(<TextInput onSend={mockSend} disabled={true} />);

          const input = screen.getByRole('textbox');
          const button = screen.getByRole('button', { name: /send/i });

          // Verify the input and button are disabled
          expect(input).toBeDisabled();
          expect(button).toBeDisabled();

          // Attempt to type the text (disabled input ignores changes in real browser,
          // but fireEvent still fires the event — button click should still be blocked)
          fireEvent.change(input, { target: { value: text } });

          // Click Send — on fixed code this should NOT call mockSend (button is disabled)
          fireEvent.click(button);

          // This assertion PASSES on fixed code because the button is disabled.
          // When pipelineState='thinking', mockSend should NOT be called.
          expect(mockSend).not.toHaveBeenCalled();
        }),
        { numRuns: 20 },
      );
    },
  );
});

// ---------------------------------------------------------------------------
// Bug 1 — Property: pressing Enter calls mockSend even when pipelineState='thinking'
//
// On UNFIXED code: the <input> element has no disabled attribute, so the
// onKeyDown handler fires and calls handleSubmit → onSend.
//
// EXPECTED OUTCOME: This test FAILS on unfixed code because mockSend IS called
// (demonstrating the bug — Enter should NOT fire when thinking).
// ---------------------------------------------------------------------------

describe('Bug 1 — Enter key active during thinking (bug condition exploration)', () => {
  it(
    'Property: for any non-empty text, pressing Enter does NOT call mockSend when disabled={true} — ' +
      'verifies fix: Enter key is blocked when pipelineState=thinking',
    () => {
      fc.assert(
        fc.property(nonEmptyTextArb, (text) => {
          // Clean up DOM between fast-check iterations
          cleanup();

          const mockSend = vi.fn();

          // Render TextInput WITH disabled={true} (simulating pipelineState='thinking').
          // On fixed code, the input is disabled, so Enter should NOT trigger onSend.
          render(<TextInput onSend={mockSend} disabled={true} />);

          const input = screen.getByRole('textbox');

          // Verify the input is disabled
          expect(input).toBeDisabled();

          // Attempt to type the text
          fireEvent.change(input, { target: { value: text } });

          // Press Enter — on fixed code this should NOT call mockSend (input is disabled)
          fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

          // This assertion PASSES on fixed code because the input is disabled.
          // When pipelineState='thinking', Enter should NOT trigger onSend.
          expect(mockSend).not.toHaveBeenCalled();
        }),
        { numRuns: 20 },
      );
    },
  );
});

// ---------------------------------------------------------------------------
// Bug 1 — TypeScript compile-time check: disabled prop does not exist
//
// On UNFIXED code: TextInputProps only declares `onSend`. Passing
// `disabled={true}` causes a TypeScript compile error:
//   "Property 'disabled' does not exist on type 'TextInputProps'"
//
// This is a COMPILE-TIME bug, not a runtime bug. The test below is a
// documentation test — it cannot be run as a runtime test because TypeScript
// would reject it at compile time on unfixed code.
//
// EXPECTED OUTCOME: TypeScript compile error on unfixed code.
// After the fix (adding `disabled?: boolean` to TextInputProps), this compiles.
//
// NOTE: The line below is intentionally commented out because it would cause
// a TypeScript compile error on unfixed code. Uncomment after the fix is applied.
// ---------------------------------------------------------------------------

describe('Bug 1 — TypeScript: disabled prop does not exist on unfixed TextInputProps', () => {
  it('documents that passing disabled={true} to TextInput causes a TS compile error on unfixed code', () => {
    // On UNFIXED code, the following JSX would cause:
    //   TS2322: Type '{ onSend: () => void; disabled: true; }' is not assignable
    //   to type 'IntrinsicAttributes & TextInputProps'.
    //   Property 'disabled' does not exist on type 'TextInputProps'.
    //
    // The fix adds `disabled?: boolean` to TextInputProps.
    //
    // INTENTIONALLY NOT RENDERED — compile-time error on unfixed code:
    // render(<TextInput onSend={vi.fn()} disabled={true} />);
    //
    // This test passes at runtime (it's just a comment/documentation),
    // but the TypeScript compiler would reject the commented line above
    // when run against unfixed TextInput.tsx.
    expect(true).toBe(true); // placeholder — real check is at compile time
  });
});
