import logging
import re
import time
from typing import Optional, List, Dict, Any
from github import Github
from github.PullRequest import PullRequest
from google import genai
from google.genai import types

from src.config import TommiConfig
from src.rules_loader import load_all_rules
from src.repo_tools import WorkspaceInspector
from src.models_resolver import resolve_candidate_models
from src.reviewer import extract_retry_delay

logger = logging.getLogger("tommi.chat")


class TommiConversationHandler:
    def __init__(
        self,
        config: TommiConfig,
        github_client: Github,
        auth_token: Optional[str] = None,
        workspace_dir: Optional[str] = None,
    ):
        self.config = config
        self.g = github_client
        self.auth_token = auth_token or config.github_token
        self.client = genai.Client(api_key=config.gemini_api_key)
        self.inspector = WorkspaceInspector(workspace_dir=workspace_dir)

    def _extract_response_text(self, response: Any) -> str:
        """Extracts text from response, excluding thought parts and handling candidate structure."""
        if not response:
            return ""
        candidate = response.candidates[0] if (hasattr(response, "candidates") and response.candidates) else None
        if not candidate:
            return response.text.strip() if hasattr(response, "text") and response.text else ""

        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) if content else None
        if parts:
            text_parts = []
            for part in parts:
                is_thought = bool(getattr(part, "thought", False))
                p_text = getattr(part, "text", None)
                if isinstance(p_text, str) and p_text and not is_thought:
                    text_parts.append(p_text)
            if text_parts:
                return "".join(text_parts).strip()

        return response.text.strip() if hasattr(response, "text") and response.text else ""

    def _generate_response(self, prompt: str) -> str:
        """Generates conversational response using candidate models with graceful failover."""
        candidate_models = resolve_candidate_models(self.client, self.config.model_name)
        for i, model_name in enumerate(candidate_models):
            logger.info(f"Generating conversation reply with model '{model_name}'...")
            try:
                gen_config = types.GenerateContentConfig(
                    temperature=0.3,
                    response_mime_type="text/plain",
                    max_output_tokens=4096,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                )
                if getattr(self.config, "thinking_budget", 0) > 0:
                    gen_config.thinking_config = types.ThinkingConfig(
                        thinking_budget=self.config.thinking_budget
                    )

                response = self.client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=gen_config,
                )
                text = self._extract_response_text(response)
                if text:
                    return text
            except Exception as e:
                error_str = str(e).lower()
                if "thinking" in error_str or "unsupported" in error_str:
                    logger.info(f"Model '{model_name}' does not support thinking_config. Retrying without it...")
                    gen_config.thinking_config = None
                    try:
                        response = self.client.models.generate_content(
                            model=model_name,
                            contents=prompt,
                            config=gen_config,
                        )
                        text = self._extract_response_text(response)
                        if text:
                            return text
                    except Exception as retry_err:
                        logger.warning(f"Retry without thinking failed: {retry_err}")
                logger.warning(f"Conversation generation failed with model '{model_name}': {e}")

        return (
            "🤖 **T.O.M.M.I.**: I'm currently experiencing high demand or temporary rate limits on my reasoning models. "
            "Please ping me again in a few moments!"
        )

    def handle_discussion(
        self,
        pr: PullRequest,
        comment_body: str,
        comment_author: str,
        in_reply_to_id: Optional[int] = None,
        file_path: Optional[str] = None,
        diff_hunk: Optional[str] = None,
    ) -> str:
        """
        Handles an interactive discussion ping (@tommi or @t-o-m-m-i-ai-reviewer)
        from a contributor or maintainer on a PR review comment or issue comment.
        Open to all contributors.
        """
        logger.info(f"Handling discussion from @{comment_author} on PR #{pr.number}...")

        # 1. Clean comment body (strip the @ping)
        cleaned_body = re.sub(r"@(?:t-o-m-m-i-ai-reviewer|tommi)\b", "", comment_body, flags=re.IGNORECASE).strip()
        if not cleaned_body:
            cleaned_body = "Hello! How can I help with this code review finding?"

        # 2. Build thread dialogue history
        thread_dialogue: List[str] = []
        if in_reply_to_id:
            try:
                # Fetch root comment
                root_comment = pr.get_comment(in_reply_to_id)
                author = root_comment.user.login if root_comment.user else "author"
                thread_dialogue.append(f"[@{author} (Review Finding)]:\n{root_comment.body}")

                # Fetch any existing replies in this same review thread
                for rc in pr.get_review_comments():
                    if getattr(rc, "in_reply_to_id", None) == in_reply_to_id:
                        rc_author = rc.user.login if rc.user else "user"
                        thread_dialogue.append(f"[@{rc_author}]:\n{rc.body}")
            except Exception as e:
                logger.warning(f"Could not load full review comment thread #{in_reply_to_id}: {e}")

        # 3. Fetch surrounding code context from file if available
        surrounding_code = ""
        if file_path:
            try:
                file_content = self.inspector.read_file(file_path)
                if file_content:
                    surrounding_code = file_content[:3000]
            except Exception as e:
                logger.debug(f"Could not inspect file context for {file_path}: {e}")

        # 4. Load rules
        rules = load_all_rules()

        # 5. Build prompt
        prompt = f"""You are T.O.M.M.I. (Tommy's Online Minecraft Modding Intelligence), an autonomous AI code review assistant specialized in Minecraft and NeoForge modding standards.
A developer (@{comment_author}) has pinged you directly on GitHub to discuss a review finding, ask a question, or clarify why their code is written the way it is.

### REVIEW THREAD CONTEXT:
- Repository: {self.config.github_repository}
- PR: #{pr.number} - {pr.title}
- Target File: {file_path or 'N/A'}

### DIFF HUNK:
```diff
{diff_hunk or 'N/A'}
```

### SURROUNDING FILE CONTEXT:
```java
{surrounding_code or 'N/A'}
```

### THREAD DISCUSSION HISTORY:
{chr(10).join(thread_dialogue) if thread_dialogue else 'No prior thread comments'}

### LATEST MESSAGE FROM @{comment_author}:
"{cleaned_body}"

### APPLICABLE REPOSITORY & MODDING RULES:
{rules.format_for_prompt()}

### INSTRUCTIONS:
1. Speak as T.O.M.M.I. in a helpful, collaborative, and technically precise tone.
2. If the user is asking a question (e.g. why something was flagged or what the rule says):
   - Explain clearly with technical reasoning, citing the specific rule and Minecraft/NeoForge lifecycle or architecture rationale.
3. If the user is explaining why the code is intentional, safe, or an acceptable exception:
   - Carefully evaluate their argument against the code context and the rules.
   - If they are correct (e.g. runs strictly on client, handled by another event, or intentional design choice), graciously acknowledge it:
     "Thanks for explaining, @{comment_author}! That makes sense because [reason]..."
   - Note that if they'd like this exception permanently recorded so you don't flag it in future reviews, they (or a maintainer) can reply with:
     `/tommi learn <rule summary>`
4. If their explanation is still problematic (e.g. potential side-safety crash on dedicated servers, memory leak, or concurrency issue):
   - Politely explain the risk and suggest an idiomatic alternative.
5. Keep your response concise, polite, and actionable. Do NOT repeat the entire diff or dump irrelevant rules.
"""

        return self._generate_response(prompt)
