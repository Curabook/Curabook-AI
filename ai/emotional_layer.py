"""
ai/emotional_layer.py
═══════════════════════════════════════════════════════════════════════════
PHI Emotional Intelligence Layer — Research-Based Implementation

Built on five psychological pillars from diabesity care research:
  1. Responsive Listening Buffer (acknowledge before advising)
  2. SDT-Based Motivation (Competence, Autonomy, Relatedness)
  3. Non-Stigmatizing Language Protocol
  4. Normalization & Psychological Safety
  5. Adaptive Emotional Arousal (rule-based emotion detection)

This module sits BETWEEN user input and the LLM call.
It does three things:
  A. Classifies the emotional state of the user's message
  B. Selects the correct acknowledgment strategy
  C. Injects the acknowledgment into the system prompt context

CRITICAL: This layer provides emotional SUPPORT, not therapy.
          It never diagnoses mental health conditions.
          It always bridges to the clinical layer, not away from it.
═══════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# Emotion Classification (Rule-Based — no external model required)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class EmotionSignal:
    primary:    str          # "frustrated" | "exhausted" | "ashamed" | "anxious" | "hopeless" | "neutral" | "positive"
    intensity:  str          # "high" | "medium" | "low"
    triggers:   list[str]    # specific phrases that fired
    food_noise: bool         # GLP-1 specific: emotional eating signal
    identity_threat: bool    # "I'm failing" / "I'm a bad patient"
    autonomy_loss: bool      # "I have no control" / "nothing works"


# Signal patterns — ordered by specificity (most specific first)
_EMOTION_PATTERNS = {
    "hopeless": [
        r"\b(nothing works|nothing is working|giving up|what's the point|no hope|hopeless|pointless)\b",
        r"\b(never going to|never get better|always going to be|stuck like this forever)\b",
        r"\b(tried everything|nothing helps|doesn't matter what i do)\b",
    ],
    "ashamed": [
        r"\b(i'm (a )?failure|i failed|i keep failing|bad patient|can't do anything right)\b",
        r"\b(i'm weak|i have no willpower|i cheated|i slipped|i messed up|i blew it)\b",
        r"\b(i should (know better|be able to|have|be doing))\b",
        r"\b(ashamed|embarrassed|disgusted with myself|hate myself)\b",
        r"\b(i'm (so )?stupid|what's wrong with me)\b",
    ],
    "frustrated": [
        r"\b(so frustrated|so annoying|drives me crazy|can't stand|fed up|sick of)\b",
        r"\b(why (doesn't|won't|can't|isn't)|doesn't make sense|doesn't work)\b",
        r"\b(doing everything right (and|but)|following (the )?plan (and|but))\b",
        r"\b(numbers (aren't|don't)|results (aren't|don't)|scale (won't|doesn't))\b",
        r"\b(not fair|not right|not working)\b",
    ],
    "exhausted": [
        r"\b(tired of|exhausted|burned out|worn out|drained|can't keep up|too much)\b",
        r"\b(tired of (tracking|logging|counting|testing|managing|fighting))\b",
        r"\b(so much (work|effort|energy)|takes (so much|too much))\b",
        r"\b(all day every day|constant(ly)?|never a break|never stops)\b",
        r"\b(overwhelmed|can't cope|too hard|too difficult|too demanding)\b",
    ],
    "anxious": [
        r"\b(worried|scared|terrified|afraid|fear|anxious|nervous|panicking)\b",
        r"\b(what if|going to (happen|get worse|develop|lead to))\b",
        r"\b(complications|heart attack|stroke|kidney|blindness|amputation)\b",
        r"\b(insurance|denied|can't afford|too expensive|cost|coverage)\b",
    ],
    "food_noise": [
        r"\b(can't stop (thinking about|eating)|food (obsession|thoughts|noise))\b",
        r"\b(cravings|urge to eat|keep eating|binge|emotional eating|stress eat)\b",
        r"\b(ate (when|even though|despite)|couldn't resist|gave in|weakness)\b",
        r"\b(food is all i think about|obsessed with food|can't focus on anything else)\b",
    ],
    "positive": [
        r"\b(doing well|feeling good|great news|excited|happy|proud|managed to)\b",
        r"\b(finally|improvement|getting better|working|progress|achieved)\b",
        r"\b(thank(s| you)|appreciate|helpful|love (this|it)|amazing)\b",
    ],
}

_IDENTITY_THREAT_PATTERNS = [
    r"\b(i('m| am) (a )?failure|i('m| am) failing)\b",
    r"\b(i('m| am) (so )?bad at this|i('m| am) terrible at)\b",
    r"\b(what('s| is) wrong with me|why can('t| not) i)\b",
    r"\b(i('m| am) (just |so )?(weak|lazy|undisciplined|bad))\b",
    r"\b(i give up|i('ve| have) given up|i quit)\b",
]

_AUTONOMY_LOSS_PATTERNS = [
    r"\b(no control|out of control|can't control)\b",
    r"\b(nothing i do (matters|helps|works))\b",
    r"\b(told (what|how) to|have to do what|no choice)\b",
    r"\b(body (won't|doesn't) listen|body (hates|fighting) me)\b",
]

_FOOD_NOISE_PATTERNS = [
    r"\b(can't stop (thinking about food|eating))\b",
    r"\b(food (noise|thoughts|obsession))\b",
    r"\b(thinking about food (all|constantly|non-stop))\b",
    r"\b(emotional (eating|hunger)|stress (eating|hunger))\b",
]


def classify_emotion(message: str) -> EmotionSignal:
    """
    Classify the emotional state of a user message using rule-based patterns.
    Returns an EmotionSignal with primary emotion, intensity, and flags.
    
    No ML model required — fast, deterministic, privacy-safe.
    """
    lower = message.lower()
    
    detected: dict[str, list[str]] = {}
    for emotion, patterns in _EMOTION_PATTERNS.items():
        matches = []
        for pattern in patterns:
            found = re.findall(pattern, lower)
            matches.extend(found if isinstance(found[0], str) else [m[0] for m in found] if found else [])
        if matches:
            detected[emotion] = matches

    # Determine primary emotion (priority order)
    priority = ["hopeless", "ashamed", "food_noise", "exhausted", "frustrated", "anxious", "positive"]
    primary = "neutral"
    triggers = []
    for emotion in priority:
        if emotion in detected:
            primary = emotion
            triggers = detected[emotion]
            break

    # Intensity from message length + exclamation + caps
    caps_ratio  = sum(1 for c in message if c.isupper()) / max(len(message), 1)
    exclamations = message.count("!") + message.count("?")
    word_count  = len(message.split())
    
    if primary in ("hopeless", "ashamed") or caps_ratio > 0.3 or exclamations >= 2:
        intensity = "high"
    elif primary in ("exhausted", "frustrated") or exclamations == 1 or word_count > 40:
        intensity = "medium"
    else:
        intensity = "low"

    food_noise = bool(re.search("|".join(_FOOD_NOISE_PATTERNS), lower))
    if "food_noise" in detected:
        food_noise = True

    identity_threat = any(
        re.search(p, lower) for p in _IDENTITY_THREAT_PATTERNS
    )
    autonomy_loss = any(
        re.search(p, lower) for p in _AUTONOMY_LOSS_PATTERNS
    )

    return EmotionSignal(
        primary        = primary,
        intensity      = intensity,
        triggers       = triggers[:3],
        food_noise     = food_noise,
        identity_threat = identity_threat,
        autonomy_loss  = autonomy_loss,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Responsive Listening Buffer — Acknowledgment Strategies
# ══════════════════════════════════════════════════════════════════════════════

# Research basis: "separate acknowledging thoughts/feelings from providing advice"
# Rule: Acknowledge FIRST, clinical content SECOND. Always.

_ACKNOWLEDGMENT_STRATEGIES = {
    "hopeless": {
        "opening": [
            "Managing a chronic condition when progress feels invisible — that exhaustion is real, and it makes complete sense that you'd feel this way.",
            "When you're doing everything right and still not seeing the changes you hoped for, the hopelessness that sets in isn't a character flaw. It's a completely understandable response to a genuinely difficult situation.",
            "That feeling of 'nothing works' is one of the hardest parts of managing a metabolic condition — not because you're doing something wrong, but because these conditions are genuinely complex.",
        ],
        "bridge": "Let's look at what your data is actually showing, because sometimes what feels like 'nothing working' has a specific, addressable cause.",
        "sdt_need": "relatedness",
        "socratic_pivot": True,
    },
    "ashamed": {
        "opening": [
            "Managing diabesity is a complex biological challenge — not a test of your willpower or character. The fact that it's hard doesn't mean you're failing.",
            "I want to be direct about something: what you're describing isn't failure. It's a person navigating one of the most demanding chronic conditions there is, with imperfect information and a body that doesn't always cooperate.",
            "Your worth isn't measured by your lab numbers or your adherence to a plan. Managing this condition is hard, full stop — and the fact that you're still here, still trying to understand it, is something.",
        ],
        "bridge": "Let's separate what the biology is doing from how you're interpreting it — because those two things are getting mixed up here.",
        "sdt_need": "competence",
        "socratic_pivot": True,
    },
    "frustrated": {
        "opening": [
            "I can see how much effort you've been putting into your management — and it's genuinely frustrating when the numbers don't reflect that work.",
            "That frustration makes complete sense. When you're doing the right things and the results don't match, it feels like the system is broken. Sometimes it is.",
            "Putting in the work and not seeing it reflected in the data is one of the most demoralizing experiences in managing a chronic condition. Your frustration is earned.",
        ],
        "bridge": "Let's look at what might actually be happening — because there's usually a specific reason, and it's rarely 'you're not trying hard enough.'",
        "sdt_need": "competence",
        "socratic_pivot": False,
    },
    "exhausted": {
        "opening": [
            "Managing this daily is a genuine juggling act — tracking, logging, monitoring, planning. It's natural to feel completely drained sometimes.",
            "The mental load of managing a metabolic condition is enormous and almost entirely invisible to people who haven't done it. Being exhausted by it isn't weakness.",
            "What you're describing — the constant vigilance, the tracking, the effort — that's real work. Feeling worn down by it doesn't mean you're doing it wrong.",
        ],
        "bridge": "You don't have to solve everything today. Let's focus on just one thing that might actually reduce the load.",
        "sdt_need": "autonomy",
        "socratic_pivot": False,
    },
    "anxious": {
        "opening": [
            "Those worries make sense — managing a chronic condition means living with real uncertainty, and that's genuinely hard.",
            "It makes sense that you're thinking about what could go wrong. The goal is to make sure you have the clearest possible picture of where things actually stand.",
            "Anxiety about complications is one of the most common experiences for people managing metabolic conditions. You're not alone in this.",
        ],
        "bridge": "Let's look at the actual data together — not to dismiss what you're feeling, but because specific numbers usually tell a less scary story than our worst-case thinking.",
        "sdt_need": "competence",
        "socratic_pivot": False,
    },
    "food_noise": {
        "opening": [
            "Food noise — the constant intrusive thoughts about eating — is a recognized and extremely common experience for people on GLP-1 medications or managing metabolic conditions. It's not a character flaw.",
            "What you're describing has a name: food noise. It's a physiological experience, not a moral one. It's your brain doing something it was trained to do, and it can be addressed.",
            "The relationship between food thoughts, stress, and blood sugar is genuinely complex. What you're experiencing is a recognized pattern, and there are specific approaches that help.",
        ],
        "bridge": "Before we look at the data, I want to ask you something: what was actually happening right before those thoughts started?",
        "sdt_need": "autonomy",
        "socratic_pivot": True,  # Pivot to Socratic questioning for food noise
    },
    "neutral": {
        "opening": [],  # No acknowledgment needed for neutral queries
        "bridge": "",
        "sdt_need": "competence",
        "socratic_pivot": False,
    },
    "positive": {
        "opening": [
            "That's genuinely worth acknowledging — progress in managing a metabolic condition is hard-won.",
            "I can see that. That kind of progress reflects real, consistent effort.",
        ],
        "bridge": "",
        "sdt_need": "competence",
        "socratic_pivot": False,
    },
}

# Socratic questions for pivoting from data-logging to reflection
_SOCRATIC_QUESTIONS = {
    "food_noise": [
        "What was happening in your day right before those thoughts started?",
        "When you notice that pattern, what's usually going on emotionally?",
        "What do you think your body might actually be needing in those moments?",
        "If the food thoughts had a message, what would they be saying?",
    ],
    "hopeless": [
        "What does 'working' actually look like for you — what would need to be different?",
        "If you think back to when things felt more manageable, what was different then?",
        "What's the one thing, if it improved, that would make the biggest difference to how you feel day-to-day?",
    ],
    "ashamed": [
        "If a close friend described the same situation to you, what would you tell them?",
        "What would it mean if this difficulty isn't about your character at all, but just about how hard this condition is?",
        "What's one thing you've actually managed well this week, even if it doesn't feel like much?",
    ],
    "exhausted": [
        "What's the most draining part of this — if you could remove one thing from the routine, what would it be?",
        "What would 'good enough' look like, instead of perfect?",
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
# Non-Stigmatizing Language Validator
# ══════════════════════════════════════════════════════════════════════════════

# Research: "weight stigma and shame-based messaging are primary drivers of app abandonment"

_STIGMATIZING_PATTERNS = [
    # Diagnostic labeling
    (re.compile(r'\b(you are|you\'re|they are|they\'re)\s+(diabetic|obese|overweight|morbidly obese)\b', re.I),
     "person with diabetes / person living with obesity"),
    
    # Blame language
    (re.compile(r'\b(you (should|shouldn\'t|need to|must|have to|ought to))\b', re.I),
     "collaborative suggestion"),
    
    # Calorie shame
    (re.compile(r'\b(went over (your )?calorie|exceeded (your )?limit|too many calories|bad food choice|cheat(ed|ing))\b', re.I),
     "energy-neutral language"),
    
    # Failure language
    (re.compile(r'\b(you failed|you\'re failing|lack of (willpower|discipline|control))\b', re.I),
     "challenge framing"),
    
    # Good/bad food moralizing
    (re.compile(r'\b((that\'s |that is )?(a )?(very )?(bad|terrible|horrible|awful) (food|choice|decision))\b', re.I),
     "neutral nutritional language"),
    
    # Command language
    (re.compile(r'\b^(eat|stop eating|avoid|don\'t eat|cut out|eliminate)\b', re.I),
     "collaborative suggestion"),
]

_REPLACEMENT_SUGGESTIONS = {
    "person with diabetes / person living with obesity": 
        "Use 'person with diabetes' or 'person managing obesity' — person-first language",
    "collaborative suggestion": 
        "Rephrase as 'you might consider' or 'one option is' or 'how do you feel about'",
    "energy-neutral language": 
        "Use 'your body had different energy needs today' — remove caloric morality",
    "challenge framing": 
        "Use 'managing this condition is genuinely hard' — remove personal failure framing",
    "neutral nutritional language": 
        "Use 'that has a higher glycemic load' — factual, not moral",
}


def validate_response_language(text: str) -> tuple[str, list[str]]:
    """
    Check AI response for stigmatizing language and flag or replace it.
    Returns (cleaned_text, list_of_flags).
    
    This runs on the LLM OUTPUT before it reaches the user.
    """
    flags = []
    cleaned = text
    
    for pattern, category in _STIGMATIZING_PATTERNS:
        if pattern.search(cleaned):
            flags.append(f"Stigmatizing language detected [{category}]: {_REPLACEMENT_SUGGESTIONS.get(category, 'Review language')}")
    
    return cleaned, flags


# ══════════════════════════════════════════════════════════════════════════════
# SDT Motivation Layer — Competence, Autonomy, Relatedness
# ══════════════════════════════════════════════════════════════════════════════

def build_sdt_context(
    signal:        EmotionSignal,
    health_context: str,
    user_name:     str = "",
) -> str:
    """
    Build the SDT-based motivational context block to inject into the prompt.
    Addresses the specific psychological need identified by the emotion classifier.
    """
    name_prefix = f"{user_name}" if user_name else "this person"
    parts = []

    if signal.identity_threat:
        parts.append(
            "IDENTITY PROTECTION REQUIRED: The user has expressed language suggesting "
            "they view this as a personal failure or character flaw. "
            "The response MUST separate the biological challenge from personal worth. "
            "Use: 'Managing diabesity is a complex biological challenge, not a personal failure.' "
            "Never use language that reinforces self-blame."
        )

    if signal.autonomy_loss:
        parts.append(
            "AUTONOMY RESTORATION REQUIRED: The user is expressing a loss of agency. "
            "The response MUST offer genuine choices, not recommendations that feel like commands. "
            "Use collaborative language: 'How do you feel about...', 'What are your thoughts on...', "
            "'One option would be — but you know your situation best.' "
            "Avoid any directive language (must, should, need to, have to)."
        )

    if signal.food_noise:
        parts.append(
            "FOOD NOISE PROTOCOL: The user is experiencing food noise or emotional eating patterns. "
            "DO NOT pivot to tracking or logging. DO NOT mention calorie counts. "
            "Instead, use Socratic questioning to surface the underlying emotional state. "
            "Validate that food noise is a recognized physiological experience, not a moral failure. "
            "Ask what was happening before the pattern started."
        )

    sdt_need = _ACKNOWLEDGMENT_STRATEGIES.get(signal.primary, {}).get("sdt_need", "competence")
    
    if sdt_need == "competence":
        parts.append(
            "SDT — COMPETENCE FOCUS: Acknowledge any small wins or effort visible in the data. "
            "Build self-efficacy by helping the user see that they DO have relevant information "
            "and that specific, achievable changes are available to them. "
            "Use: 'I can see from your data that...' to anchor competence in facts."
        )
    elif sdt_need == "autonomy":
        parts.append(
            "SDT — AUTONOMY FOCUS: The response must support the user's sense of control. "
            "Offer options not directives. End with a question that invites their perspective. "
            "Validate that they get to decide what changes to make and when."
        )
    elif sdt_need == "relatedness":
        parts.append(
            "SDT — RELATEDNESS FOCUS: The user needs to feel less alone. "
            "Use 'we' language naturally: 'Let's look at this together', 'We can work on this.' "
            "Explicitly note that their experience is shared by many people managing this condition. "
            "Position PHI as a supportive partner — not a tool, not a judge."
        )

    return "\n".join(parts) if parts else ""


# ══════════════════════════════════════════════════════════════════════════════
# Normalization Templates
# ══════════════════════════════════════════════════════════════════════════════

_NORMALIZATION_STATEMENTS = {
    "logging_fatigue": 
        "Managing this daily — the logging, the tracking, the monitoring — is a genuine full-time job that most people don't see. Many people find it completely overwhelming at times. That's not weakness; it's a realistic response to a demanding condition.",
    "weight_plateau": 
        "Weight plateaus during metabolic management are extremely common and have biological explanations. They aren't a sign that something is wrong with your effort or commitment.",
    "med_adherence": 
        "Missing a dose happens to virtually everyone managing a chronic condition. The research on medication adherence shows it's one of the most universal challenges — not a personal failing.",
    "emotional_eating": 
        "The relationship between stress, emotions, and eating is physiological, not a matter of willpower. Many people find this one of the hardest aspects of metabolic management to navigate.",
    "number_anxiety": 
        "Checking your numbers and feeling anxious about them is a completely understandable response. Many people managing metabolic conditions describe the same experience.",
    "general": 
        "Many people managing metabolic conditions describe feeling exactly what you're describing. You're not alone in this, even when it feels that way.",
}


def get_normalization_statement(signal: EmotionSignal, user_message: str) -> str:
    """Select the most appropriate normalization statement based on context."""
    lower = user_message.lower()
    
    if signal.food_noise or "eat" in lower or "food" in lower:
        return _NORMALIZATION_STATEMENTS["emotional_eating"]
    if "log" in lower or "track" in lower or "count" in lower or signal.primary == "exhausted":
        return _NORMALIZATION_STATEMENTS["logging_fatigue"]
    if "weight" in lower or "scale" in lower or "plateau" in lower:
        return _NORMALIZATION_STATEMENTS["weight_plateau"]
    if "missed" in lower or "forgot" in lower or "medication" in lower or "med" in lower:
        return _NORMALIZATION_STATEMENTS["med_adherence"]
    if signal.anxious or "worried" in lower or "scared" in lower:
        return _NORMALIZATION_STATEMENTS["number_anxiety"]
    if signal.primary in ("hopeless", "ashamed", "frustrated"):
        return _NORMALIZATION_STATEMENTS["general"]
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# Master Prompt Injection Builder
# ══════════════════════════════════════════════════════════════════════════════

def build_emotional_context(
    user_message:   str,
    health_context: str = "",
    user_name:      str = "",
) -> tuple[str, EmotionSignal]:
    """
    Master function. Call this before building the LLM message list.
    
    Returns:
        (emotional_context_block: str, signal: EmotionSignal)
    
    The emotional_context_block is injected as a system message layer
    between the core PHI system prompt and the health context.
    
    Usage in chat_routes.py:
        from ai.emotional_layer import build_emotional_context, validate_response_language
        
        emotional_ctx, signal = build_emotional_context(message, health_context, user_name)
        # inject emotional_ctx into messages before health memory
        
        # After LLM call:
        reply, flags = validate_response_language(reply)
        if flags:
            print(f"[EMOTIONAL] Language flags: {flags}")
    """
    signal = classify_emotion(user_message)
    
    # No emotional layer needed for neutral or positive queries
    if signal.primary in ("neutral", "positive") and not signal.identity_threat and not signal.autonomy_loss and not signal.food_noise:
        return "", signal

    parts = []
    
    # Layer 1: Responsive Listening Directive
    strategy = _ACKNOWLEDGMENT_STRATEGIES.get(signal.primary, _ACKNOWLEDGMENT_STRATEGIES["neutral"])
    
    if strategy["opening"]:
        # Pick acknowledgment based on intensity
        idx = 0 if signal.intensity == "high" else 1 if signal.intensity == "medium" else 2
        idx = min(idx, len(strategy["opening"]) - 1)
        acknowledgment = strategy["opening"][idx]
        
        parts.append(
            "━━━ EMOTIONAL ACKNOWLEDGMENT LAYER ━━━\n"
            "RESPONSIVE LISTENING PROTOCOL — APPLY BEFORE ANY CLINICAL CONTENT:\n\n"
            f"This user is expressing: [{signal.primary.upper()}] intensity:[{signal.intensity.upper()}]\n\n"
            f"MANDATORY OPENING for this response:\n\"{acknowledgment}\"\n\n"
            f"BRIDGE to clinical content:\n\"{strategy['bridge']}\"\n\n"
            "RULE: The acknowledgment MUST come before any data, numbers, or advice. "
            "Do not modify the acknowledgment to be shorter. Do not skip straight to clinical content."
        )

    # Layer 2: Normalization statement
    normalization = get_normalization_statement(signal, user_message)
    if normalization:
        parts.append(
            f"NORMALIZATION STATEMENT (weave naturally into response, not as a separate paragraph):\n"
            f"\"{normalization}\""
        )

    # Layer 3: SDT motivation context
    sdt_ctx = build_sdt_context(signal, health_context, user_name)
    if sdt_ctx:
        parts.append(f"SDT MOTIVATION DIRECTIVES:\n{sdt_ctx}")

    # Layer 4: Socratic pivot for food noise / hopeless / ashamed
    if strategy.get("socratic_pivot") and signal.primary in _SOCRATIC_QUESTIONS:
        questions = _SOCRATIC_QUESTIONS[signal.primary]
        q = questions[0]
        parts.append(
            f"SOCRATIC PIVOT: After the acknowledgment and any relevant clinical data, "
            f"end the response with this question rather than more advice:\n"
            f"\"{q}\"\n"
            f"This question should feel natural, not clinical. It invites reflection."
        )

    # Layer 5: Language constraints
    constraints = [
        "NON-STIGMATIZING LANGUAGE — MANDATORY CONSTRAINTS:",
        "• Never use: 'you should', 'you must', 'you need to', 'you have to' — replace with collaborative framing",
        "• Never use: 'diabetic', 'obese patient' — use 'person with diabetes', 'person managing obesity'",
        "• Never use: 'bad food', 'cheat', 'went over your limit' — use neutral, biological language",
        "• Never use: 'failure', 'lack of willpower', 'discipline' — these are shame triggers",
        "• Do use: 'person-first language', 'complex biological challenge', 'hard to manage'",
        "• Do use: 'we', 'together', 'let's look at' — relatedness language",
        "• Tone: warm, specific, grounded in data — never cheerful, never dismissive",
    ]
    parts.append("\n".join(constraints))

    full_context = "\n\n".join(parts) + "\n━━━ END EMOTIONAL LAYER ━━━"
    return full_context, signal