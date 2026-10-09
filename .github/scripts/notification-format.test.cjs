const { test } = require('node:test');
const assert = require('node:assert/strict');
const { buildMarkdown, buildNotification } = require('./notification-format.cjs');
const channels = ['discord', 'slack', 'wechat'];
const fixture = {
  REPO: 'edgefn/demo', SERVICE_NAME: 'r9s-prod-api', PLATFORM: 'SAE',
  ENV_NAME: 'prod', REGION: 'cn-test', SHA: '123456789', RELEASE_TAG: 'v1',
  ACTOR: 'tester', BUILD_STATUS: 'success', DEPLOY_STATUS: 'success',
  PR_AUTHOR: 'author', PR_TITLE: 'Merge changes', PR_URL: 'https://example.com/pr/12',
  HEAD_REF: 'feature', BASE_REF: 'main', DEFAULT_BRANCH: 'main',
  DELETED_COUNT: '2', DELETED_BRANCHES: '  feature/a\nfeature/b  ',
  RUN_URL: 'https://example.com/run',
};
function content(channel, payload) {
  if (channel === 'discord') return payload.embeds[0].title + '\n' + payload.embeds[0].description;
  if (channel === 'slack') return payload.text + '\n' + payload.attachments[0].text;
  return payload.markdown.content;
}
for (const channel of channels) {
  for (const type of ['deploy', 'deploy_result', 'build', 'merge', 'branch_cleanup']) {
    test(`${channel}: ${type} preserves fields and one workflow link`, () => {
      const value = content(channel, buildNotification(channel, { ...fixture, NOTIFY_TYPE: type }));
      assert.ok(value.includes('edgefn/demo'));
      assert.equal((value.match(/查看工作流/g) || []).length, 1);
      assert.ok(!value.includes('r9s-prod-api'));
      if (type === 'branch_cleanup') {
        assert.ok(value.includes('🧹 已清理已合并分支'));
        assert.ok(value.includes('feature/a\nfeature/b'));
        assert.ok(value.includes('删除数量'));
      }
    });
  }
  test(`${channel}: cleanup empty options and default count`, () => {
    const value = content(channel, buildNotification(channel, {
      NOTIFY_TYPE: 'branch_cleanup', DELETED_BRANCHES: ' \n ', DELETED_COUNT: '',
    }));
    assert.ok(value.includes('：0'));
    assert.ok(!value.includes('已删除分支'));
    assert.ok(!value.includes('查看工作流'));
  });
}
test('fallback repository, SHA and actor when input is empty', () => {
  const value = buildMarkdown({ NOTIFY_TYPE: 'deploy', REPO: '', SHA: '', ACTOR: '',
    GITHUB_REPOSITORY: 'owner/repo', GITHUB_SHA: 'abcdef12345', GITHUB_ACTOR: 'bot' });
  for (const expected of ['owner/repo', 'abcdef1', 'bot']) assert.ok(value.includes(expected));
});
test('Slack formatting and escaping', () => {
  const payload = buildNotification('slack', { ...fixture, NOTIFY_TYPE: 'merge', PR_TITLE: '<x> & y' });
  assert.ok(payload.text.includes('<https://example.com/pr/12|12>'));
  assert.ok(payload.attachments[0].text.includes('&lt;x&gt; &amp; y'));
  assert.ok(!payload.attachments[0].text.includes('**'));
});
test('status colors include cancellation and build failure takes priority', () => {
  const env = { ...fixture, NOTIFY_TYPE: 'deploy_result', BUILD_STATUS: 'failure' };
  assert.equal(buildNotification('discord', env).embeds[0].color, 15548997);
  assert.equal(buildNotification('slack', env).attachments[0].color, '#ED4245');
  env.BUILD_STATUS = 'success'; env.DEPLOY_STATUS = 'cancelled';
  assert.equal(buildNotification('discord', env).embeds[0].color, 10070709);
  assert.equal(buildNotification('slack', env).attachments[0].color, '#99AAB5');
  assert.ok(buildNotification('wechat', env).markdown.content.includes('部署已取消'));
});
test('Discord limits', () => {
  const embed = buildNotification('discord', { ...fixture, NOTIFY_TYPE: 'merge',
    PR_NUMBER: '1'.repeat(300), PR_TITLE: 'a'.repeat(5000) }).embeds[0];
  assert.equal(embed.title.length, 256);
  assert.equal(embed.description.length, 4096);
});
test('unknown notification keeps merge fallback; unknown channel fails', () => {
  assert.equal(buildMarkdown({ ...fixture, NOTIFY_TYPE: 'unknown' }), buildMarkdown({ ...fixture, NOTIFY_TYPE: 'merge' }));
  assert.throws(() => buildNotification('unknown'), /Unsupported/);
});
