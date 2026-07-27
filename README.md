# ✈️ Amex JourneyGuard
### Autonomous Travel-Disruption Concierge

An AI-powered autonomous travel assistant that detects flight disruptions in real time and proactively rebooks flights, reschedules hotels, and keeps card members informed—all while following travel policies and requiring human approval only when necessary.

---

## 📖 Overview

Flight disruptions are one of the biggest pain points in modern travel. Delays, cancellations, diversions, and missed connections often require passengers to manually search for alternatives, contact airlines, and rearrange hotel bookings.

**Amex JourneyGuard** is an agentic system that continuously monitors a travel itinerary, detects disruptions, evaluates whether intervention is required, and autonomously performs the necessary actions within predefined policy limits.

The system follows a **deterministic-first** philosophy:
- Use business rules wherever decisions are clear.
- Invoke an LLM only when reasoning or ambiguity is involved.

---

## 🚨 Problem Statement

Current travel assistance suffers from several limitations:

- Manual rebooking is slow and reactive.
- Missed connections are usually detected too late.
- Insurance benefits are claimed only after the disruption occurs.
- Human concierge services are expensive and difficult to scale.
- No existing solution continuously monitors an itinerary, makes policy-aware decisions, and acts autonomously.

---

# 💡 Solution

JourneyGuard continuously monitors a member's itinerary and:

- Detects cancellations, delays, diversions, and operational changes.
- Predicts missed-connection risk before it happens.
- Decides whether rebooking is required.
- Searches and ranks alternative flights.
- Automatically books eligible itineraries.
- Escalates only when approval is needed.
- Updates hotels whenever required.
- Notifies the traveler throughout the entire journey.

---

# 🏗️ System Architecture

The system is designed as independent event-driven modules.

```
Flight Monitoring
        │
        ▼
Decision Agent
        │
        ▼
Rebooking Agent
        │
 ┌──────┴────────┐
 ▼               ▼
Hotel Agent   Notification System
        │
        ▼
Itinerary State Manager
        │
        ▼
Card Member Interface
```

Major modules include:

- Monitoring & Detection
- Decision Agent
- Rebooking Agent
- Hotel Rescheduling Agent
- Notification System
- Card Member Interface
- Itinerary State Management
- Escalation & Fallback

---

# 🤖 AI Agents

## 1. Decision Agent

Responsible for deciding whether rebooking should be initiated.

Uses:

- Flight disruption data
- Missed connection calculations
- Weather information
- Airport congestion
- Airline OTP statistics

The Decision Agent only invokes an LLM for ambiguous or medium-severity situations.

Responsibilities:

- Decide if rebooking is necessary
- Resolve conflicting data
- Trigger the Rebooking Agent
- Generate user-friendly explanations

---

## 2. Rebooking Agent

Handles the complete downstream workflow after approval.

Responsibilities:

- Search alternate flights
- Rank available options
- Verify policy compliance
- Book flights
- Hold seats when approval is required
- Retry failed operations
- Roll back safely when necessary

---

# ⚙️ End-to-End Workflow

1. Monitor flight status continuously.
2. Detect disruptions.
3. Calculate missed-connection risk.
4. Route directly to rebooking for high-severity cases.
5. Use LLM reasoning for medium-severity cases.
6. Search and rank alternate flights.
7. Book automatically if policy allows.
8. Otherwise request member approval.
9. Update hotel reservations.
10. Notify the member throughout the process.
11. Close the disruption after every domain is resolved.

---

# ✈️ Flight Monitoring

The monitoring service performs adaptive polling based on departure time.

| Time to Departure | Poll Frequency |
|------------------|---------------|
| < 3 hours | 60 sec |
| < 12 hours | 5 min |
| < 48 hours | 30 min |
| > 48 hours | 4 hr |

Supported disruption events:

- Flight cancellation
- Flight delay
- Diversion
- Gate change
- Terminal change

---

# 🧠 Missed Connection Engine

The system estimates connection risk using:

- Flight buffer time
- Minimum Connection Time (MCT)
- Airport-specific transfer constraints

Possible outputs:

- Safe
- Watch
- High Risk
- Missed Connection Confirmed

This enables proactive intervention before passengers actually miss their flights.

---

# 🎯 Rebooking Strategy

Alternate flights are ranked using deterministic scoring based on:

- Price difference
- Arrival delay
- Cabin class match
- Number of stops
- Reconnection risk

The system then checks policy limits before deciding whether booking can proceed automatically.

---

# 🛡️ Policy Engine

Every booking is validated against configurable card policies.

Examples include:

- Maximum automatic spending limits
- Allowed cabin classes
- Tier-based approval thresholds
- Hotel budget constraints

Only compliant bookings are automatically confirmed.

---

# 🔄 Reliability Features

JourneyGuard is designed to be production-friendly.

Features include:

- Redis distributed locks
- PostgreSQL audit logging
- Event idempotency
- Saga rollback pattern
- Retry handling
- Circuit breaker strategy
- State machine tracking
- Asynchronous reconciliation

---

# 📲 Notification System

Supports multiple communication channels.

- Push Notifications (FCM)
- SMS (Twilio)
- Email (SendGrid)
- Live WebSocket updates

The notification layer also uses an LLM for:

- Human-friendly explanations
- Understanding free-text replies
- Structured response classification

---

# 🛠️ Tech Stack

### Backend
- Python
- FastAPI

### AI
- LangGraph
- LangChain
- Claude

### Database
- PostgreSQL

### Cache
- Redis

### Mobile
- Flutter

### APIs

- Duffel
- AviationStack
- Twilio
- SendGrid
- Firebase Cloud Messaging

> The APIs and technology choices were selected primarily because they provide generous free tiers. The architecture has been designed so that enterprise-grade APIs can be integrated in the future with minimal changes.

---

# 📈 Expected Performance

- < 15 second flight search latency
- > 70% automatic booking rate
- 100% rollback correctness
- < 30 second approval-to-booking time after user response

---

# 🚀 Future Improvements

- Production airline APIs
- Enterprise booking integrations
- Multi-passenger itinerary support
- Real airline PNR management
- Improved disruption prediction models
- Advanced recommendation engine

---

# ⚠️ Project Status

This project was developed as part of the **American Express Autonomous Travel Disruption Concierge Hackathon**.

Due to the limited hackathon timeline, we were unable to integrate every module into a single end-to-end runnable application.

However, this repository contains the implementation of all major components—including the agents, tools, backend services, APIs, and user interface. Every module has been implemented and tested independently, and the architecture has been designed for straightforward integration into a complete production pipeline.

---
