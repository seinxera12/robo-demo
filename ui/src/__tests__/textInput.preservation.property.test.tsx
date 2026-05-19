/**
 * Preservation Property Tests — Non-Buggy Input Behavior
 *
 * These tests capture the CORRECT behavior that must be preserved after the fix.
 * They MUST PASS on unfixed TextInput.tsx.
 *
 * Observation: on unfixed code, TextInput has no `disabled` prop.
 * The tests below test the component WITHOUT a disabled prop (current API).
 * They verify the existing behavior works correctly for non-buggy inputs.
 *
 * Validates: AC-1.3, AC-1.4, AC-1.5, AC-3.1, AC-3.3
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

/** Any whitespace-only string (empty or spaces/tabs/newlines) */
const whitespaceOnlyTextArb = fc.oneof(
  fc.constant(''),
  fc.stringMatching(/^[ \t\n\r]+$/),
);

// ---------------------------------------------------------------------------
// Preservation Property 1:
// For any non-empty string and pipelineState in ['listening', 'speaking', 'disconnected'],
// clicking Send calls onSend exactly once with the trimmed text.
//
// On UNFIXED code: TextInput has no `disabled` prop, so we simulate these
// states by simply NOT passing disabled (the current behavior). The component
// should work normally for all non-thinking states.
//
// EXPECTED OUTCOME: PASSES on unfixed code (confirms baseline behavior).
// ---------------------------------------------------------------------------

describe(
  'Preservation — Property 1: Send calls onSend exactly once with trimmed text ' +
    '(pipelineState not thinking)',
  () => {
    it(
      'Property: for any non-empty text and pipelineState in ' +
        "['listening', 'speaking', 'disconnected'], clicking Send calls onSend once with trimmed text",
      () => {
        fc.assert(
          fc.property(
            nonEmptyTextArb,
            fc.constantFrom('listening', 'speaking', 'disconnected'),
            (text, _pipelineState) => {
              // Clean up DOM between fast-check iterations
              cleanup();

              const mockSend = vi.fn();

              // Render TextInput with NO disabled prop (current API on unfixed code).
              // These pipelineState values are non-buggy — the component should work normally.
              render(<TextInput onSend={mockSend} />);

              const input = screen.getByRole('textbox');
              const button = screen.getByRole('button', { name: /send/i });

              // Type the text
              fireEvent.change(input, { target: { value: text } });

              // Click Send — should call onSend exactly once with trimmed text
              fireEvent.click(button);

              // Preservation: onSend is called exactly once
              expect(mockSend).toHaveBeenCalledTimes(1);

              // Preservation: onSend is called with the trimmed text
              expect(mockSend).toHaveBeenCalledWith(text.trim());
            },
          ),
          { numRuns: 50 },
        );
      },
    );

    it(
      'Property: for any non-empty text, pressing Enter calls onSend exactly once with trimmed text ' +
        '(pipelineState not thinking)',
      () => {
        fc.assert(
          fc.property(
            nonEmptyTextArb,
            fc.constantFrom('listening', 'speaking', 'disconnected'),
            (text, _pipelineState) => {
              cleanup();

              const mockSend = vi.fn();

              render(<TextInput onSend={mockSend} />);

              const input = screen.getByRole('textbox');

              // Type the text
              fireEvent.change(input, { target: { value: text } });

              // Press Enter — should call onSend exactly once with trimmed text
              fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

              // Preservation: onSend is called exactly once
              expect(mockSend).toHaveBeenCalledTimes(1);

              // Preservation: onSend is called with the trimmed text
              expect(mockSend).toHaveBeenCalledWith(text.trim());
            },
          ),
          { numRuns: 50 },
        );
      },
    );
  },
);

// ---------------------------------------------------------------------------
// Preservation Property 2:
// For any empty/whitespace-only text, the Send button is disabled.
//
// On UNFIXED code: TextInput has no `disabled` prop. The existing
// `disabled={!value.trim()}` guard is the only gate. This test verifies
// that guard is preserved and works correctly.
//
// Note: on unfixed code there is no `disabled` prop — we test the
// empty-field guard only (which already works).
//
// EXPECTED OUTCOME: PASSES on unfixed code (confirms baseline behavior).
// ---------------------------------------------------------------------------

describe(
  'Preservation — Property 2: Send button is disabled for empty/whitespace-only text ' +
    '(existing !value.trim() guard preserved)',
  () => {
    it(
      'Property: for any empty or whitespace-only text, the Send button is disabled ' +
        '(existing guard preserved)',
      () => {
        fc.assert(
          fc.property(whitespaceOnlyTextArb, (text) => {
            cleanup();

            const mockSend = vi.fn();

            // Render TextInput with NO disabled prop (current API on unfixed code).
            render(<TextInput onSend={mockSend} />);

            const input = screen.getByRole('textbox');
            const button = screen.getByRole('button', { name: /send/i });

            // Set the text value (empty or whitespace-only)
            fireEvent.change(input, { target: { value: text } });

            // Preservation: Send button is disabled when text is empty/whitespace
            expect(button).toBeDisabled();

            // Preservation: clicking the disabled button does NOT call onSend
            fireEvent.click(button);
            expect(mockSend).not.toHaveBeenCalled();
          }),
          { numRuns: 50 },
        );
      },
    );

    it('Send button is disabled on initial render (empty value)', () => {
      cleanup();

      const mockSend = vi.fn();
      render(<TextInput onSend={mockSend} />);

      const button = screen.getByRole('button', { name: /send/i });

      // Preservation: button starts disabled (empty field)
      expect(button).toBeDisabled();
    });
  },
);

// ---------------------------------------------------------------------------
// Preservation Property 3:
// pipelineState === 'speaking' → Send button is enabled and callable
// (barge-in allowed from UI — the lock is 'thinking'-only).
//
// On UNFIXED code: TextInput has no `disabled` prop, so the button is
// always enabled when text is non-empty. This test verifies that the
// 'speaking' state does NOT disable the button (barge-in must remain possible).
//
// EXPECTED OUTCOME: PASSES on unfixed code (confirms baseline behavior).
// ---------------------------------------------------------------------------

describe(
  "Preservation — Property 3: pipelineState === 'speaking' → Send button enabled " +
    '(barge-in allowed, lock is thinking-only)',
  () => {
    it(
      "Property: for any non-empty text and pipelineState === 'speaking', " +
        'Send button is enabled and onSend is callable',
      () => {
        fc.assert(
          fc.property(nonEmptyTextArb, (text) => {
            cleanup();

            const mockSend = vi.fn();

            // Render TextInput with NO disabled prop (current API on unfixed code).
            // pipelineState='speaking' — barge-in must be allowed.
            render(<TextInput onSend={mockSend} />);

            const input = screen.getByRole('textbox');
            const button = screen.getByRole('button', { name: /send/i });

            // Type the text
            fireEvent.change(input, { target: { value: text } });

            // Preservation: button is NOT disabled during 'speaking'
            expect(button).not.toBeDisabled();

            // Preservation: clicking Send calls onSend (barge-in works)
            fireEvent.click(button);
            expect(mockSend).toHaveBeenCalledTimes(1);
            expect(mockSend).toHaveBeenCalledWith(text.trim());
          }),
          { numRuns: 50 },
        );
      },
    );
  },
);
