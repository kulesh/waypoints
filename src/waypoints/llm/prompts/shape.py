"""SHAPE phase prompts for idea brief and product spec generation."""
from __future__ import annotations

# System prompt for brief generation
BRIEF_SYSTEM_PROMPT = (
    "You are a technical writer creating concise product documentation."
)

# System prompt for spec generation
SPEC_SYSTEM_PROMPT = (
    "You are a senior product manager creating detailed "
    "product specifications. Be thorough but practical."
)

# System prompt for summary generation
SUMMARY_SYSTEM_PROMPT = "You are a concise technical writer. Write plain prose."

BRIEF_GENERATION_PROMPT = """\
Based on the ideation conversation below, generate a concise Idea Brief document.

The brief should be in Markdown format and include:

# Idea Brief: [Catchy Title]

## Problem Statement
What problem are we solving and why does it matter?

## Target Users
Who are the primary users and what are their pain points?

## Proposed Solution
High-level description of what we're building.

## Key Features
- Bullet points of core capabilities

## Success Criteria
How will we know if this succeeds?

## Open Questions
Any unresolved items that need further exploration.

---

Keep it concise (under 500 words). Focus on clarity over completeness.
The goal is to capture the essence of the idea so others can quickly understand it.

Here is the ideation conversation:

{conversation}

Generate the Idea Brief now:"""

BRIEF_SUMMARY_PROMPT = """\
Based on this idea brief, write a concise 100-150 word summary that captures:
- What the project is
- The core problem it solves
- Key features

Write in third person, present tense. No markdown formatting, no headers,
just plain prose. This summary will be shown in a project list view.

Idea Brief:
{brief_content}

Write the summary now (100-150 words):"""

SPEC_GENERATION_PROMPT = """\
Based on the Idea Brief below, generate a comprehensive Product Specification.

The specification should be detailed enough for engineers and product managers
to understand exactly what needs to be built. Use Markdown format.

# Product Specification: [Product Name]

## 1. Executive Summary
Brief overview of the product and its value proposition.

## 2. Problem Statement
### 2.1 Current Pain Points
### 2.2 Impact of the Problem
### 2.3 Why Now?

## 3. Target Users
### 3.1 Primary Persona
### 3.2 Secondary Personas
### 3.3 User Journey

## 4. Product Overview
### 4.1 Vision Statement
### 4.2 Core Value Proposition
### 4.3 Key Differentiators

## 5. Features & Requirements
### 5.1 MVP Features (Must Have)
### 5.2 Phase 2 Features (Should Have)
### 5.3 Future Considerations (Nice to Have)

## 6. Technical Considerations
### 6.1 Architecture Overview
### 6.2 Technology Stack Recommendations
### 6.3 Integration Requirements
### 6.4 Security & Privacy

## 7. Success Metrics
### 7.1 Key Performance Indicators
### 7.2 Success Criteria for MVP

## 8. Risks & Mitigations
### 8.1 Technical Risks
### 8.2 Market Risks
### 8.3 Mitigation Strategies

## 9. FAQ
Common questions and answers for the development team.

## 10. Appendix
### 10.1 Glossary
### 10.2 References

---

Here is the Idea Brief to expand:

{brief}

Generate the complete Product Specification now:"""

SPEC_SUMMARY_PROMPT = """\
Based on this product specification, write a polished 200-250 word summary
that captures:
- What the product is and does
- The problem it solves and for whom
- Key features and differentiators
- Technical approach (briefly)

Write in third person, present tense. No markdown formatting, no headers,
just plain prose. This summary will be shown in a project list view.

Product Specification:
{spec_content}

Write the summary now (200-250 words):"""
