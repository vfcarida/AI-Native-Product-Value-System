// Allow discovery agent to query or call knowledge base in sandbox
permit (
    principal in ProductValueSystem::AgentRole::"discovery",
    action in [ProductValueSystem::Action::"query", ProductValueSystem::Action::"call"],
    resource in ProductValueSystem::Tool::"knowledge_base"
) when {
    context.environment == "sandbox"
};

// Allow specification agent to read and write specifications
permit (
    principal in ProductValueSystem::AgentRole::"specification",
    action in [ProductValueSystem::Action::"read", ProductValueSystem::Action::"write"],
    resource in ProductValueSystem::Tool::"spec_editor"
) when {
    context.environment == "sandbox" &&
    context.stage == "specification"
};

// Allow build agent to deploy to production only with explicit human approval
permit (
    principal in ProductValueSystem::AgentRole::"build",
    action == ProductValueSystem::Action::"deploy",
    resource in ProductValueSystem::Environment::"production"
) when {
    context.has_human_approval == true
};

// Deny any agent from deploying to production without approval
forbid (
    principal,
    action == ProductValueSystem::Action::"deploy",
    resource in ProductValueSystem::Environment::"production"
) unless {
    context.has_human_approval == true
};

// Allow build agent to execute code in isolated sandbox
permit (
    principal in ProductValueSystem::AgentRole::"build",
    action == ProductValueSystem::Action::"execute",
    resource in ProductValueSystem::Tool::"sandbox"
) when {
    context.environment == "sandbox" &&
    context.iteration_count < 10
};

// Require human approval and high trust for external API calls
permit (
    principal,
    action == ProductValueSystem::Action::"call",
    resource in ProductValueSystem::Tool::"external_api"
) when {
    context.has_human_approval == true &&
    principal.trustLevel == "high"
};

// Deny agents from exceeding cost budget
forbid (
    principal,
    action,
    resource
) when {
    context.cost_so_far >= principal.costBudget
};

// Allow review agents read-only access to artifacts during review stage
permit (
    principal in ProductValueSystem::AgentRole::"architecture_review",
    action == ProductValueSystem::Action::"read",
    resource
) when {
    context.stage == "review"
};

permit (
    principal in ProductValueSystem::AgentRole::"security_review",
    action == ProductValueSystem::Action::"read",
    resource
) when {
    context.stage == "review"
};

// Deny write/execute/deploy access to review agents (read-only)
forbid (
    principal in ProductValueSystem::AgentRole::"architecture_review",
    action in [
        ProductValueSystem::Action::"write",
        ProductValueSystem::Action::"execute",
        ProductValueSystem::Action::"deploy"
    ],
    resource
);

forbid (
    principal in ProductValueSystem::AgentRole::"security_review",
    action in [
        ProductValueSystem::Action::"write",
        ProductValueSystem::Action::"execute",
        ProductValueSystem::Action::"deploy"
    ],
    resource
);
