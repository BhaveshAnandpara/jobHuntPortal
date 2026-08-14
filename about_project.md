# About Project

## Project Name

**AI-Powered Multi-Agent Career Opportunity Platform**

## Overview

This project is a generic, AI-powered career opportunity and networking platform designed to help users streamline the complete job-search lifecycle.

The platform is not limited to software engineers. It is designed to work for professionals across different domains, including:

- Software Engineering
- AI / Machine Learning
- Full-Stack Development
- Java / Spring Boot
- Mechanical Engineering
- Civil Engineering
- HR / Talent Acquisition
- Product Management
- Data Analytics
- Finance
- Design
- Students and Freshers
- Other professional roles

The system adapts to each user based on the resumes, preferences, experience, skills, and target roles they provide.

At its core, the platform combines:

- **LangGraph** for intelligent multi-agent workflow orchestration
- **Apache Kafka** for asynchronous, event-driven processing and parallel job handling
- **LLMs** for understanding resumes, job descriptions, candidate profiles, and outreach context
- **Persistent storage** for tracking opportunities, contacts, outreach, applications, and status
- **Human-in-the-loop approval** for sensitive external actions such as outreach

The objective is to transform job searching from a collection of repetitive manual tasks into a structured, intelligent, event-driven workflow.

---

# Agenda

The project aims to automate and organize the most repetitive parts of a job search while keeping important decisions under user control.

The system should help users:

1. Upload one or more resumes.
2. Automatically understand their professional profiles.
3. Discover relevant job opportunities.
4. Allow users to manually paste a job-post URL.
5. Analyze every job opportunity.
6. Compare the opportunity against all uploaded resumes.
7. Select the best-suited resume automatically.
8. Calculate an overall job-match score.
9. Ignore low-relevance opportunities.
10. Find relevant professionals who may be suitable referral contacts.
11. Rank those contacts based on relevance.
12. Generate personalized referral or networking messages.
13. Ask the user for approval before external outreach.
14. Track the complete lifecycle of every opportunity.
15. Process multiple opportunities asynchronously and in parallel.

---

# Problem Statement

Job searching involves several repetitive and disconnected activities.

A user often has to:

- Search across multiple job sources.
- Read lengthy job descriptions.
- Decide whether they are actually qualified.
- Decide which version of their resume to use.
- Find people working at the target company.
- Determine who is most likely to respond.
- Write personalized connection requests.
- Send referral emails.
- Remember who has already been contacted.
- Follow up after several days.
- Track applications, interviews, rejections, and offers.

Users with multiple professional profiles face an additional problem.

For example, one person may have different resumes for:

- AI Engineer roles
- Backend roles
- Full-Stack roles
- Java / Spring Boot roles
- DevOps roles

Manually deciding which resume best matches every job quickly becomes inefficient.

This project creates one intelligent system that handles the entire workflow.

---

# Core Product Philosophy

The platform should be:

## Generic

The system should not contain hard-coded assumptions about the user's profession.

It should not rely on logic such as:

```text
if role == software_engineer
```

Instead, the platform should understand the user dynamically from their uploaded resumes.

```text
Resume
   ↓
Profile Understanding
   ↓
Job Understanding
   ↓
Compatibility Analysis
   ↓
Decision
```

---

## Profile-Aware

A user can maintain multiple resumes representing different professional profiles.

For example:

```text
User
│
├── Resume A → AI Engineer
├── Resume B → Java Backend Engineer
├── Resume C → Full-Stack Engineer
└── Resume D → DevOps Engineer
```

Another user might have:

```text
User
│
├── Resume A → Mechanical Design Engineer
├── Resume B → Manufacturing Engineer
└── Resume C → CAD Engineer
```

The platform should work identically in both cases.

---

## Event-Driven

The architecture should use events to communicate important state changes between components.

Apache Kafka acts as the central event backbone.

Examples:

```text
jobs.discovered
jobs.matched
jobs.shortlisted
contacts.requested
contacts.found
outreach.generated
outreach.approved
outreach.sent
applications.updated
```

This keeps components loosely coupled and allows multiple consumers to react independently to the same event.

---

## Multi-Agent

Different agents should specialize in different parts of the workflow.

The system should not rely on one large agent doing everything.

Core responsibilities can include:

- Profile understanding
- Job discovery
- Job matching
- Contact discovery
- Outreach generation
- Tracking

LangGraph coordinates the decision-making and reasoning flow between these agents.

---

## Parallel

Independent work should be processed concurrently.

If the system discovers 100 jobs, it should not need to process all 100 sequentially.

Kafka consumer groups can distribute jobs among multiple workers.

```text
                    jobs.discovered
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
          Worker 1     Worker 2     Worker 3
              │            │            │
           Job #1        Job #2       Job #3
           Job #4        Job #5       Job #6
           Job #7        Job #8       Job #9
```

LangGraph can also fan out independent tasks and merge results later.

---

## Human-in-the-Loop

The system should automate research, analysis, ranking, and message generation.

However, important external actions should remain under user control.

For example:

```text
Generate Outreach
       ↓
Human Review
   /       \
Approve    Edit / Reject
   │
   ▼
Send
```

This prevents unwanted or excessive outreach and gives users control over professional communication.

---

# Main User Journeys

The platform has three primary entry points.

```text
                         USER
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
   Upload Resume      Paste Job URL    Automatic Search
          │                │                │
          ▼                │                │
  Profile Analyzer         │                │
          │                │                │
          ▼                │                │
     Profile Store         │                │
          │                │                │
          └────────────────┼────────────────┘
                           ▼
                  Opportunity Workflow
```

---

# User Journey 1: Upload Resumes

A first-time user uploads one or more resumes.

```text
START
 │
 ▼
Upload Resume(s)
 │
 ▼
Resume Analysis
 │
 ▼
Extract Candidate Information
 │
 ├── Skills
 ├── Experience
 ├── Education
 ├── Projects
 ├── Certifications
 ├── Industry
 ├── Seniority
 └── Possible Target Roles
 │
 ▼
Create Candidate Profiles
 │
 ▼
Store Profiles
```

The user can upload additional resumes later.

New resumes should be analyzed and added as additional candidate profiles.

Previously discovered opportunities may also be re-evaluated against the newly added profile.

---

# User Journey 2: Paste a Job URL

The user may independently find an interesting job online.

Instead of manually analyzing it, the user simply pastes the URL.

```text
User Pastes Job URL
        │
        ▼
Job Ingestion
        │
        ▼
Extract Job Information
        │
        ├── Company
        ├── Role
        ├── Location
        ├── Description
        ├── Skills
        ├── Experience
        └── Job URL
        │
        ▼
Publish Job Event
        │
        ▼
jobs.discovered
```

From this point onward, the manual job follows exactly the same workflow as automatically discovered jobs.

The system should automatically determine which resume is best suited.

The user should not need to manually specify:

> Use my Java resume.

---

# User Journey 3: Automatic Job Discovery

The system should also be able to discover relevant opportunities automatically.

The discovery process uses the user's profiles and preferences as context.

```text
Candidate Profiles
        +
Search Preferences
        │
        ▼
Job Discovery Agent
        │
        ▼
Potential Opportunities
        │
        ▼
jobs.discovered
```

The Job Discovery Agent is responsible only for finding potential opportunities.

It should not make the final decision about whether a job is worth applying to.

That responsibility belongs to the matching stage.

---

# Profile and Resume Management

The system should allow users to:

- Upload multiple resumes.
- Add a new resume at any time.
- Remove an existing resume.
- Replace an outdated resume.
- Maintain multiple professional profiles.
- Review the profiles inferred by the system.
- Associate preferences with individual profiles.

Example:

```text
Candidate
│
├── Profile 1
│   ├── AI Engineer Resume
│   ├── Python
│   ├── LLM
│   ├── RAG
│   └── AI Tooling
│
├── Profile 2
│   ├── Java Backend Resume
│   ├── Java
│   ├── Spring Boot
│   ├── Kafka
│   └── AWS
│
└── Profile 3
    ├── Full-Stack Resume
    ├── React
    ├── TypeScript
    ├── Node.js
    └── REST APIs
```

Profiles should be generated dynamically.

---

# Job Matching

The Job Matching Agent receives a discovered opportunity and compares it against every relevant user profile.

Example:

```text
Job:

Backend Engineer

Requirements:
Java
Spring Boot
Kafka
AWS
REST APIs
PostgreSQL
```

The matching stage might produce:

```text
AI Engineer Resume        → 63%
Full-Stack Resume         → 74%
Java Backend Resume       → 92%
DevOps Resume             → 69%
```

The system then chooses:

```text
Selected Resume: Java Backend Resume
Match Score: 92%
Decision: SHORTLIST
```

The matching stage should evaluate factors such as:

- Required skills
- Preferred skills
- Experience level
- Domain relevance
- Role similarity
- Education requirements
- Location
- Seniority
- Candidate preferences
- Resume relevance
- Missing skills

The result becomes structured data that the rest of the system can consume.

---

# Opportunity Filtering

Not every discovered job should trigger the complete workflow.

The matching stage acts as a gate.

```text
              Job
               │
               ▼
          Match Agent
               │
               ▼
           Match Score
               │
          ┌────┴────┐
          ▼         ▼
      Relevant   Irrelevant
          │         │
          ▼         ▼
     Continue     Ignore
```

This prevents unnecessary contact discovery, LLM calls, and outreach generation for poor matches.

---

# Contact Discovery

Once an opportunity is shortlisted, the platform should identify relevant people associated with the company.

The type of people searched should depend on the role.

For a software engineering opportunity:

```text
Software Engineers
Senior Engineers
Engineering Managers
Technical Leads
Recruiters
```

For a mechanical engineering opportunity:

```text
Mechanical Engineers
Design Leads
Engineering Managers
Manufacturing Leads
Recruiters
```

For an HR opportunity:

```text
HR Managers
Talent Acquisition Leads
HR Business Partners
Recruiters
Department Leaders
```

The platform should infer the most relevant contact types dynamically.

---

# Contact Ranking

Finding employees is not enough.

The system should rank contacts based on their likelihood of being useful for networking or referral outreach.

Possible signals include:

```text
Same Company
      +
Relevant Department
      +
Role Similarity
      +
Seniority
      +
Technical / Professional Similarity
      +
Connection Proximity
      +
Recent Professional Activity
      ↓
Contact Relevance Score
```

The result could look like:

```text
Contact A
Senior Software Engineer
Referral Potential: 9.1 / 10

Contact B
Software Engineer
Referral Potential: 8.7 / 10

Contact C
Technical Recruiter
Referral Potential: 7.5 / 10
```

---

# Outreach Generation

The Outreach Agent generates personalized professional communication.

Input context can include:

```text
Candidate Profile
        +
Selected Resume
        +
Target Job
        +
Target Company
        +
Contact Profile
        ↓
Personalized Outreach
```

Possible output types include:

- Connection request
- Direct message
- Referral request
- Recruiter outreach
- Email
- Follow-up message

The goal is to avoid generic mass outreach.

---

# Application Tracking

The system should maintain a persistent record for every opportunity.

Possible tracked fields include:

```text
Company
Role
Location
Job URL
Source
Discovery Date
Job Description
Match Score
Selected Resume
Matched Skills
Missing Skills
Referral Contacts
Connection Status
Email Status
Referral Status
Application Status
Applied Date
Follow-Up Date
Interview Status
Rejection
Offer
Notes
```

The tracker becomes the central source of truth for the user's job search.

---

# Opportunity Lifecycle

A typical opportunity can move through states such as:

```text
DISCOVERED
     │
     ▼
MATCHED
     │
     ▼
SHORTLISTED
     │
     ▼
CONTACT_SEARCH
     │
     ▼
CONTACT_FOUND
     │
     ▼
OUTREACH_GENERATED
     │
     ▼
OUTREACH_APPROVED
     │
     ▼
OUTREACH_SENT
     │
     ▼
REFERRED
     │
     ▼
APPLIED
     │
     ▼
INTERVIEW
     │
     ├──────────► REJECTED
     │
     ▼
   OFFER
```

Not every opportunity must pass through every state.

For example, a user may apply directly without receiving a referral.

---

# Architecture

## High-Level Architecture

```text
                                USER
                                  │
          ┌───────────────────────┼───────────────────────┐
          │                       │                       │
          ▼                       ▼                       ▼
   Upload Resume(s)         Paste Job URL         Automatic Discovery
          │                       │                       │
          ▼                       │                       │
   Profile Analyzer              │                       │
          │                       │                       │
          ▼                       │                       │
     Profile Store               │                       │
          │                       │                       │
          └───────────────────────┼───────────────────────┘
                                  ▼
                         Job Ingestion Layer
                                  │
                                  ▼
                      Kafka: jobs.discovered
                                  │
                                  ▼
                         Matching Consumers
                                  │
                         LangGraph Workflow
                                  │
                       Compare User Profiles
                                  │
                                  ▼
                         Select Best Resume
                                  │
                                  ▼
                          jobs.matched
                                  │
                                  ▼
                           Relevant Job?
                            /          \
                          NO            YES
                          │              │
                       Ignore           ▼
                              jobs.shortlisted
                                      │
                           ┌───────────┴───────────┐
                           ▼                       ▼
                    Contact Discovery          Tracker
                           │                       │
                           ▼                       │
                     Rank Contacts                │
                           │                       │
                           ▼                       │
                    contacts.found                │
                           │                       │
                           ▼                       │
                     Outreach Agent               │
                           │                       │
                           ▼                       │
                   outreach.generated             │
                           │                       │
                           ▼                       │
                     Human Approval               │
                           │                       │
                           ▼                       │
                   outreach.approved              │
                           │                       │
                           ▼                       │
                     Send Outreach                │
                           │                       │
                           ▼                       │
                     outreach.sent ────────────────┘
                                   │
                                   ▼
                                Tracker
```

---

# LangGraph Responsibility

LangGraph is the reasoning and orchestration layer.

It answers:

> What should happen next?

LangGraph is responsible for:

- Agent orchestration
- LLM reasoning
- Conditional routing
- Profile selection
- Match evaluation
- Tool calling
- Workflow branching
- Human approval steps
- State transitions
- Retry decisions at the workflow level

Example:

```text
                   LangGraph
                       │
            ┌──────────┼──────────┐
            ▼          ▼          ▼
        Reasoning   Routing    Tool Usage
            │
            ▼
      Workflow Decision
```

---

# Apache Kafka Responsibility

Kafka is the asynchronous event backbone.

It answers:

> What work is available, and which consumers should process it?

Kafka is responsible for:

- Event streaming
- Asynchronous communication
- Consumer groups
- Parallel processing
- Work distribution
- Decoupling components
- Replayable events
- Retryable event processing
- Scaling workers independently

Example topics:

```text
jobs.discovered
jobs.matched
jobs.shortlisted
contacts.requested
contacts.found
outreach.generated
outreach.approved
outreach.sent
applications.updated
```

---

# Kafka and LangGraph Together

Kafka and LangGraph solve different problems.

```text
                     LANGGRAPH
                         │
                What happens next?
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
   Reasoning        Conditional        Tool
                    Routing            Calls


                       KAFKA
                         │
             What work is available?
                         │
       ┌─────────────────┼─────────────────┐
       ▼                 ▼                 ▼
    Worker 1          Worker 2          Worker 3
```

LangGraph should not be replaced by Kafka.

Kafka should also not be used between every LangGraph node unnecessarily.

A LangGraph workflow can process the intelligent decision-making for a single opportunity, while Kafka distributes many opportunities across available workers.

---

# Parallel Job Processing

Suppose automatic discovery finds 100 jobs.

The jobs are published to:

```text
jobs.discovered
```

Multiple workers belonging to the same consumer group can process those jobs concurrently.

```text
                         Kafka
                    jobs.discovered
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
       Worker 1         Worker 2         Worker 3
          │                │                │
       Job #1           Job #2           Job #3
       Job #4           Job #5           Job #6
       Job #7           Job #8           Job #9
```

Each worker may run the LangGraph matching workflow for its assigned opportunity.

This gives the system true asynchronous and parallel processing.

---

# Parallelism Inside a Workflow

LangGraph can also run independent branches concurrently.

For example, after a job is shortlisted:

```text
                         SHORTLISTED JOB
                               │
                   ┌───────────┴───────────┐
                   ▼                       ▼
            Contact Discovery        Update Tracker
                   │                       │
                   ▼                       │
             Rank Contacts                │
                   │                       │
                   └───────────┬───────────┘
                               ▼
                         Continue Workflow
```

The tracker does not need to wait for contact discovery.

This allows the system to use dependency-aware parallelism.

---

# Event Flow

A typical automatically discovered job may follow this sequence:

```text
Job Discovery Agent
        │
        ▼
jobs.discovered
        │
        ▼
Matching Consumer
        │
        ▼
LangGraph Matching Workflow
        │
        ▼
jobs.matched
        │
        ▼
Match Threshold
   /          \
 NO            YES
 │              │
 ▼              ▼
Ignore     jobs.shortlisted
                 │
                 ▼
         Contact Discovery
                 │
                 ▼
          contacts.found
                 │
                 ▼
          Outreach Agent
                 │
                 ▼
       outreach.generated
                 │
                 ▼
          Human Approval
                 │
                 ▼
       outreach.approved
                 │
                 ▼
           Send Outreach
                 │
                 ▼
         outreach.sent
                 │
                 ▼
              Tracker
```

---

# Manual Job Flow

A manually submitted job uses the same downstream pipeline.

```text
Paste Job URL
      │
      ▼
Job Ingestion
      │
      ▼
Extract Job Details
      │
      ▼
jobs.discovered
      │
      ▼
Match Profiles
      │
      ▼
Choose Best Resume
      │
      ▼
Shortlist
      │
      ▼
Find Contacts
      │
      ▼
Generate Outreach
      │
      ▼
Human Approval
      │
      ▼
Track Application
```

This means automatic discovery and manual submission share one common workflow.

---

# Suggested Platform Components

```text
Career Opportunity Platform
│
├── User Management
│
├── Resume Management
│   ├── Upload Resume
│   ├── Delete Resume
│   ├── Replace Resume
│   └── Multiple Resumes
│
├── Profile Intelligence
│   ├── Resume Parsing
│   ├── Skill Extraction
│   ├── Experience Analysis
│   └── Dynamic Profile Generation
│
├── Opportunity Discovery
│   ├── Automatic Job Search
│   └── Manual URL Submission
│
├── Job Ingestion
│
├── Multi-Agent Orchestration
│   └── LangGraph
│
├── Event Infrastructure
│   └── Apache Kafka
│
├── Job Matching
│
├── Contact Discovery
│
├── Contact Ranking
│
├── Outreach Generation
│
├── Human Approval
│
├── Application Tracking
│
├── Analytics
│
└── Dashboard
```

---

# Technology Responsibilities

| Technology | Responsibility |
|---|---|
| Python | Primary application language |
| FastAPI | API and ingestion layer |
| LangGraph | Multi-agent reasoning and workflow orchestration |
| Apache Kafka | Event streaming and asynchronous work distribution |
| PostgreSQL | Persistent platform data |
| SQLAlchemy | Database access and persistence abstraction |
| Playwright / BeautifulSoup | Job-page extraction where applicable |
| Ollama | Local LLM inference |
| Docker Compose | Local infrastructure orchestration |
| Streamlit | Initial dashboard and user interface |

The initial goal is for the core platform to be capable of running locally with minimal cost.

---

# Goals

## Primary Goals

### 1. Reduce repetitive job-search work

Automate research, comparison, contact discovery, message generation, and tracking.

### 2. Support multiple resumes

Users should not be forced into a single professional identity.

### 3. Automatically choose the best resume

Every opportunity should be compared against the user's available profiles.

### 4. Support any profession

The architecture should remain domain-independent.

### 5. Combine autonomous discovery with manual control

Users should be able to either let the system discover opportunities or paste a specific job URL.

### 6. Use event-driven architecture

Kafka should decouple components and enable asynchronous, parallel processing.

### 7. Use genuine multi-agent orchestration

LangGraph should coordinate specialized agents rather than merely chaining unrelated LLM prompts.

### 8. Keep sensitive actions user-controlled

Professional outreach should use human-in-the-loop approval.

### 9. Maintain persistent state

The system should remember every opportunity and its lifecycle.

### 10. Remain extensible

New profiles, agents, job sources, communication channels, scoring models, and analytics should be addable without redesigning the entire system.

---

# Secondary Goals

The project should also demonstrate real-world engineering concepts such as:

- Multi-agent systems
- Agentic workflows
- Event-driven architecture
- Apache Kafka
- Consumer groups
- Asynchronous processing
- Parallel workers
- LangGraph
- LLM tool calling
- Structured LLM outputs
- Semantic matching
- Human-in-the-loop workflows
- Persistent workflow state
- Distributed system design
- Service decoupling
- Retry handling
- Application lifecycle tracking
- Observability and analytics

---

# Non-Goal

The objective is not to create a bot that blindly mass-applies to jobs or sends hundreds of unsolicited networking messages.

The intended workflow is:

```text
Automate Discovery
        ↓
Automate Analysis
        ↓
Automate Ranking
        ↓
Automate Preparation
        ↓
Human Decision
        ↓
External Action
```

The platform should optimize quality and relevance rather than outreach volume.

---

# End-to-End Product Experience

The final experience should feel simple despite the complexity of the underlying architecture.

```text
                    USER
                     │
          ┌──────────┴──────────┐
          │                     │
    Upload Resumes         Existing User
          │                     │
          ▼                     │
   Build Profiles               │
          │                     │
          └──────────┬──────────┘
                     ▼
              Career Platform
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
   Discover Jobs          Paste Job URL
          │                     │
          └──────────┬──────────┘
                     ▼
                Analyze Job
                     │
                     ▼
              Compare Profiles
                     │
                     ▼
              Select Best Resume
                     │
                     ▼
                 Score Match
                     │
                     ▼
                Shortlist?
                 /       \
               NO         YES
               │           │
            Ignore         ▼
                    Find Contacts
                           │
                           ▼
                     Rank Contacts
                           │
                           ▼
                    Generate Outreach
                           │
                           ▼
                      User Approval
                           │
                           ▼
                    External Outreach
                           │
                           ▼
                    Track Application
```

---

# Project Vision

The long-term vision is to build a reusable **AI career operating system** that can support professionals across industries.

Instead of requiring users to manually coordinate job boards, resumes, networking, emails, spreadsheets, and follow-ups, the platform provides one intelligent workflow around the entire process.

The system should understand:

- Who the user is professionally
- Which profiles they can target
- Which opportunities are relevant
- Which resume best represents them for a specific role
- Who may be useful to contact
- What communication is appropriate
- What has already happened
- What needs to happen next

The result is a generic, event-driven, multi-agent career platform rather than a profession-specific job-search script.
