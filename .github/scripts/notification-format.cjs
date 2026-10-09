const ECS_PREFIXES = ['r9s-dev-', 'r9s-stag-', 'r9s-prod-'];
const STATUS_COLORS = { success: '#57F287', failure: '#ED4245', cancelled: '#99AAB5' };
const DISCORD_COLORS = { success: 5763719, failure: 15548997, cancelled: 10070709 };

// Pure formatter: no webhook credentials, I/O or GitHub context required.
function buildMarkdown(env = process.env, channel = 'discord') {
  const get = (key, fallback = '') => env[key] || fallback;
  const displaySvc = (name) => {
    const prefix = ECS_PREFIXES.find(p => name.startsWith(p));
    return prefix ? name.slice(prefix.length) : name;
  };
  const repo = () => get('REPO', get('GITHUB_REPOSITORY'));
  const sha7 = () => (get('SHA', get('GITHUB_SHA')) || '').slice(0, 7);
  const actor = () => get('ACTOR', get('GITHUB_ACTOR'));
  const statusLabel = (status, fallback = '未完成') =>
    ({ success: '成功', failure: '失败', cancelled: '已取消' })[status] || fallback;
  const builders = {
    deploy_result: () => {
      const svc = displaySvc(get('SERVICE_NAME'));
      const bs = get('BUILD_STATUS'), ds = get('DEPLOY_STATUS');
      const status = bs === 'success' ? ds : bs;
      const icon = status === 'success' ? '✅' : '❌';
      const shaFull = get('SHA', get('GITHUB_SHA')) || '';
      return (
        `### ${icon} ${get('PLATFORM')} ${svc} 部署${channel === 'wechat' ? statusLabel(status) : status === 'success' ? '成功' : '失败'}\n` +
        `**环境**：${get('ENV_NAME')}\n` +
        `**区域**：${get('REGION')}\n` +
        `**仓库**：${repo()}\n` +
        `**版本**：${get('RELEASE_TAG')} (${shaFull.slice(0, 7)})\n` +
        `**操作人**：${actor()}\n` +
        `**构建状态**：${bs}\n` +
        `**部署状态**：${ds}\n`
      );
    },
    deploy: () => {
      const svc = displaySvc(get('SERVICE_NAME'));
      return (
        `### 🚀 ${svc} 部署任务已提交\n` +
        `**环境**：${get('ENV_NAME', 'test')}\n` +
        `**区域**：${get('REGION')}\n` +
        `**仓库**：${repo()}\n` +
        `**版本**：${sha7()}\n` +
        `**操作人**：${actor()}\n` +
        `**提交状态**：${get('DEPLOY_STATUS')}\n`
      );
    },
    build: () => {
      const svc = displaySvc(get('SERVICE_NAME'));
      const ds = get('DEPLOY_STATUS');
      const icon = ds === 'success' ? '✅' : '❌';
      const label = channel === 'wechat' ? statusLabel(ds, ds || '未知') : ds === 'success' ? '成功' : ds === 'failure' ? '失败' : ds || '未知';
      return (
        `### ${icon} ${svc} 构建${label}\n` +
        `**环境**：${get('ENV_NAME', 'test')}\n` +
        `**区域**：${get('REGION')}\n` +
        `**仓库**：${repo()}\n` +
        `**版本**：${sha7()}\n` +
        `**操作人**：${actor()}\n` +
        `**构建状态**：${label}\n`
      );
    },
    branch_cleanup: () => {
      const defaultBranch = get('DEFAULT_BRANCH');
      const deletedCount = get('DELETED_COUNT') || '0';
      const deletedBranches = get('DELETED_BRANCHES').trim();
      let content = (
        `### 🧹 已清理已合并分支\n` +
        `**仓库**：${repo()}\n` +
        `**默认分支**：${defaultBranch}\n` +
        `**删除数量**：${deletedCount}\n`
      );
      if (deletedBranches) content += `**已删除分支**：\n${deletedBranches}\n`;
      return content;
    },
    merge: () => {
      const prAuthor = get('PR_AUTHOR');
      const prNumber = get('PR_NUMBER') || (get('PR_URL') ? get('PR_URL').replace(/\/$/, '').split('/').pop() : '');
      const prTitle = get('PR_TITLE');
      const prUrl = get('PR_URL');
      const headRef = get('HEAD_REF');
      const baseRef = get('BASE_REF');
      const titleLine = (prNumber && prUrl) ? `### ✅ PR [${prNumber}](${prUrl}) 已合并` : '### ✅ PR 已合并';
      return (
        `${titleLine}\n` +
        `**仓库**：${repo()}\n` +
        `**提交人**：${prAuthor}\n` +
        `**分支**：${headRef} → ${baseRef}\n` +
        `**标题**：${prTitle}\n`
      );
    },
  };

  const notifyType = get('NOTIFY_TYPE', 'merge');
  let content = (builders[notifyType] || builders.merge)();
  if (get('RUN_URL')) content += `**详情**：[查看工作流](${get('RUN_URL')})\n`;
  return content;
}

function slackMarkdown(markdown) {
  return markdown
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*([^\n]+?)\*\*/g, '*$1*')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<$2|$1>');
}

function buildNotification(channel, env = process.env) {
  if (!['discord', 'slack', 'wechat'].includes(channel)) {
    throw new Error(`Unsupported notification channel: ${channel}`);
  }
  const markdown = buildMarkdown(env, channel);
  if (channel === 'wechat') return { msgtype: 'markdown', markdown: { content: markdown } };

  const lines = markdown.trim().split('\n');
  let title = lines.shift().replace(/^### /, '').trim();
  let description = lines.join('\n').trim();
  let status = 'success';
  if (env.NOTIFY_TYPE === 'deploy_result') {
    status = env.BUILD_STATUS === 'success' ? env.DEPLOY_STATUS : env.BUILD_STATUS;
  } else if (env.NOTIFY_TYPE === 'build') {
    status = env.DEPLOY_STATUS || 'success';
  }

  if (channel === 'slack') {
    return {
      channel: '#alert', username: 'webhookbot', icon_emoji: ':ghost:',
      text: `*${slackMarkdown(title)}*`,
      attachments: [{
        color: STATUS_COLORS[status] ?? STATUS_COLORS.failure,
        text: slackMarkdown(description), mrkdwn_in: ['text'],
      }],
    };
  }

  if (title.length > 256) title = title.slice(0, 253) + '...';
  if (description.length > 4096) description = description.slice(0, 4093) + '...';
  const embed = { title, description, color: DISCORD_COLORS[status] ?? DISCORD_COLORS.failure };
  if (env.RUN_URL) embed.url = env.RUN_URL;
  return { embeds: [embed] };
}

module.exports = { buildMarkdown, buildNotification };
