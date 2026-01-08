# Waypoints

Waypoints is an AI native software development environment. The name "Waypoints" is inspired by how an autopilot flies an aircraft from origin to destination following waypoints programmed into the flight management system by the pilots. Similarly, our software development environment is going to turn ideas into waypoints and the waypoints into a product. We will use Claude Agent SDK and a Python-based TUI interface as the foundation to build on Waypoints on.

The rest of the document describes a builder's journey from an idea to the product.

## Step 1: Product Ideation

Developer enters an idea they have: e.g. "An IDE for generative software." Then Waypoints walk the developer through a Q&A session to crystallize the idea until they are satisfied. Along the way it would be good to show the developer a summary and gaps present in the ideation process. A suggested UI is below but I am sure we can make a much better UI :-)

```
-------------------------------------
| Idea: <developer enters idea here>|
|-----------------------------------|
|Q&A discussion with the developer. |
|           ......                  |
|           ......                  |
|           ......                  |
|-----------------------------------|
```

Once the Q&A is concluded, Waypoints writes an idea brief and presents it to the developer in a visually appealing format on screen (see figure below in Step 2). The developer should be able to edit the document. All iterations of the drafts along with the final version should be saved. Files should be in Markdown format.

## Step 2: Product Specification

Waypoints then takes the idea brief and turns it into a clear product specification. The product specification should have sufficient detail and clarity to be shared with other product managers and developers to get them on-boarded to the project. It would be good to have a frequently asked questions / frequently answered questions section to clarify any ambiguity or give specific guidance.

```
-------------------------------------
| Title: Waypoints, a generative IDE|
|-----------------------------------|
|Details of the product spec cleanly|
| formatted and visually appealing. |
|Also, developers can edit/change th|
|e document inline.                 |
|           ......                  |
|Sed ut perspiciatis, unde omnis ist|
|e natus error sit voluptatem accusa|
|o inventore veritatis et quasi arch|
|itecto beatae vitae dicta sunt, exp|
|           ......                  |
|           ......                  |
|-----------------------------------|
```

The developer should be able to edit the document. All iterations of the drafts along with the final version should be saved. Files should be in Markdown format.

## Step 3: Determine Waypoints

In this step Waypoints transform the product spec into a set of waypoints that build on top of each other to turn the idea into a product. The waypoints are automatically generated using Claude Agent SDK. Depending on the size of the project, the waypoints could be multi-hop (i.e. similar to EPICs in Agile Methodology) or single-hop (i.e. similar to a User Story in Agile Methodology). Each (single-hop) waypoint should be small enough for a developer to finish in a set amount of time and the completion of a waypoint should result in a testable component. Needless to say the granularity and the number of waypoints depends on the size of the project. For example, a "Hello World" program would have a single waypoint :-)

We need to present the waypoints and the details of each waypoint to the developer in a way they can comprehend the progression of their journey through the project. The developer should be able to adjust the sequencing and granularity of the waypoints as well as edit the details of each. For example, developer should be able to highlight a waypoint and say "let's break this down." Or, select multiple waypoints and say "let's merge these together." This UX needs some creativity; here is one suggestion.

```
-------------------------------------
| Waypoints    | Details            |
|-----------------------------------|
| Build this   | Sed ut perspiciatis|
|-----------------------------------|
| Build biggy  | this is a multi-hop|
|-----------------------------------|
| |-> Build dat| this is single-hop |
|-----------------------------------|
| |-> Build dat| this is single-hop |
|-----------------------------------|
| Build more   | Sed ut perspiciatis|
|-----------------------------------|
|           ......                  |
|           ......                  |
|-----------------------------------|
```

The waypoints should be stored in a JSONL file with all appropriate metadata. All iterations of the drafts along with the final version should be saved. When Waypoints is started on an existing project this file will be used to bring the system into a continuation state. Before moving for Step 4, Waypoints should commit all artifacts produced thus far.

## Step 4: Autopilot Engaged

Once the developer and Waypoints agree on the waypoints then it is a matter of
executing on each waypoint, one at time. For each waypoint coding agent steps
through the following:

1.  Write the tests
2.  Write the code
3.  Run the tests and iterate until tests pass
4.  Run the linters, stylers etc. on the code
5.  AUAT - Automated User Acceptance Test
6.  Update any relevant documentation
7.  Commit code, and all artifacts (e.g. test results, AUAT certification, etc)
8.  Mark the waypoint as done

It is important we keep track of the mapping between each line of code written
for the specific waypoint. Developers should not worry about the underlying
version control operations. We should indicate clearly when a waypoint is
completed and when it was "committed." Developers should be able to rewind and
fast-forward through the versions of the "journey" using the waypoints alone
(without git checkout commit-id)

```
-------------------------------------
| Name of Waypoint, being built     |
|-----------------------------------|
|Code snipets and other details from|
|the underlying coding agent/model. |
|           ......                  |
|Sed ut perspiciatis, unde omnis ist|
|e natus error sit voluptatem accusa|
|o inventore veritatis et quasi arch|
|itecto beatae vitae dicta sunt, exp|
|           ......                  |
|-----------------------------------|
```

UX should be innovative in this step. The developer should be able to monitor
what's currently happening but also should be able to toggle between completed
and pending waypoints.

We should also be providing a screen to show the developer where we are in the
journey so they can orient themselves.

## Step 5: Landing the Aircraft

Once all waypoints are successfully completed we have landed the airplane at the
destination. We can now inform the developer to do a test drive of the product
built by Waypoints.
