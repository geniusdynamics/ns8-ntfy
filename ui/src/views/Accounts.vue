<!--
  Copyright (C) 2026 Genius Dynamics
  SPDX-License-Identifier: GPL-3.0-or-later
  Adapted from Platypuschan/ns8-ntfy-reworked (GPL-3.0).
-->
<template>
  <cv-grid fullWidth>
    <cv-row
      ><cv-column class="page-title"
        ><h2>{{ $t("accounts.title") }}</h2></cv-column
      ></cv-row
    >
    <cv-row
      ><cv-column
        ><cv-tile light>
          <p class="mg-bottom">{{ $t("accounts.description") }}</p>
          <NsInlineNotification
            v-if="error"
            kind="error"
            :title="$t('accounts.title')"
            :description="error"
            :showCloseButton="false"
          />
          <NsInlineNotification
            v-if="!authEnabled"
            kind="info"
            :title="$t('accounts.auth_disabled')"
            :description="$t('accounts.auth_disabled_help')"
            :showCloseButton="false"
          />
          <NsInlineNotification
            v-if="restartFailed"
            kind="warning"
            :title="$t('accounts.restart_failed')"
            :showCloseButton="false"
          />
          <div v-if="credential" class="credential mg-bottom" role="status">
            <p>
              {{
                $t("accounts.new_credential", { username: credential.username })
              }}
            </p>
            <code>{{
              credential.kind === "password" || visible
                ? credential.value
                : "••••••••••••"
            }}</code>
            <NsButton
              v-if="credential.kind === 'token'"
              kind="ghost"
              size="small"
              @click="visible = !visible"
              >{{ $t(visible ? "accounts.hide" : "accounts.show") }}</NsButton
            >
            <NsButton kind="ghost" size="small" @click="copyCredential">{{
              $t(copied ? "accounts.copied" : "accounts.copy")
            }}</NsButton>
            <NsButton kind="ghost" size="small" @click="clearCredential">{{
              $t("accounts.dismiss")
            }}</NsButton>
          </div>
          <h4 class="mg-bottom">{{ $t("accounts.users") }}</h4>
          <div class="table-wrapper mg-bottom">
            <table class="bx--data-table bx--data-table--compact">
              <thead>
                <tr>
                  <th>{{ $t("accounts.username") }}</th>
                  <th>{{ $t("accounts.role") }}</th>
                  <th>{{ $t("accounts.actions") }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="user in namedUsers" :key="user.username">
                  <td>
                    {{ user.username
                    }}<span v-if="user.provisioned">
                      ({{ $t("accounts.provisioned") }})</span
                    >
                  </td>
                  <td>{{ $t("accounts." + user.role) }}</td>
                  <td>
                    <NsButton
                      kind="ghost"
                      size="small"
                      :disabled="locked || user.provisioned"
                      @click="
                        mutate({
                          operation: 'reset-password',
                          username: user.username,
                        })
                      "
                      >{{ $t("accounts.regenerate") }}</NsButton
                    >
                    <NsButton
                      kind="danger--ghost"
                      size="small"
                      :disabled="
                        locked || user.provisioned || isLastAdmin(user)
                      "
                      @click="removeUser(user)"
                      >{{ $t("accounts.remove") }}</NsButton
                    >
                  </td>
                </tr>
                <tr v-if="!namedUsers.length">
                  <td colspan="3">{{ $t("accounts.no_users") }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p class="mg-bottom">{{ $t("accounts.provisioned_help") }}</p>
          <cv-form @submit.prevent="addUser" class="mg-bottom">
            <cv-text-input
              v-model.trim="username"
              :label="$t('accounts.username')"
              :disabled="locked"
              :invalid-message="usernameError"
              class="mg-bottom"
            />
            <label class="bx--label" for="new-role">{{
              $t("accounts.role")
            }}</label>
            <select
              id="new-role"
              v-model="role"
              class="bx--select-input mg-bottom"
              :disabled="locked"
            >
              <option value="user">{{ $t("accounts.user") }}</option>
              <option value="admin">{{ $t("accounts.admin") }}</option>
            </select>
            <NsButton kind="primary" :disabled="locked">{{
              $t("accounts.add_user")
            }}</NsButton>
          </cv-form>
          <h4 class="mg-bottom">{{ $t("accounts.tokens") }}</h4>
          <p class="mg-bottom">{{ $t("accounts.tokens_help") }}</p>
          <div class="table-wrapper mg-bottom">
            <table class="bx--data-table bx--data-table--compact">
              <thead>
                <tr>
                  <th>{{ $t("accounts.username") }}</th>
                  <th>{{ $t("accounts.label") }}</th>
                  <th>{{ $t("accounts.token") }}</th>
                  <th>{{ $t("accounts.expires") }}</th>
                  <th>{{ $t("accounts.actions") }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="token in tokens" :key="token.id">
                  <td>{{ token.username }}</td>
                  <td>{{ token.label }}</td>
                  <td>••••••••••••</td>
                  <td>{{ token.expires }}</td>
                  <td>
                    <NsButton
                      kind="danger--ghost"
                      size="small"
                      :disabled="locked || token.provisioned"
                      @click="removeToken(token)"
                      >{{ $t("accounts.revoke") }}</NsButton
                    >
                  </td>
                </tr>
                <tr v-if="!tokens.length">
                  <td colspan="5">{{ $t("accounts.no_tokens") }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <cv-form @submit.prevent="addToken" class="mg-bottom">
            <label class="bx--label" for="token-user">{{
              $t("accounts.username")
            }}</label>
            <select
              id="token-user"
              v-model="tokenUser"
              class="bx--select-input mg-bottom"
              :disabled="locked || !namedUsers.length"
            >
              <option
                v-for="user in namedUsers"
                :key="user.username"
                :value="user.username"
              >
                {{ user.username }}
              </option>
            </select>
            <cv-text-input
              v-model.trim="label"
              :label="$t('accounts.label')"
              :disabled="locked"
              maxlength="80"
              class="mg-bottom"
            />
            <label class="bx--label" for="token-expiry">{{
              $t("accounts.expires")
            }}</label>
            <select
              id="token-expiry"
              v-model="expires"
              class="bx--select-input mg-bottom"
              :disabled="locked"
            >
              <option
                v-for="expiry in ['never', '1d', '7d', '30d', '90d', '365d']"
                :key="expiry"
                :value="expiry"
              >
                {{ expiry === "never" ? $t("accounts.never") : expiry }}
              </option>
            </select>
            <NsButton kind="primary" :disabled="locked || !tokenUser">{{
              $t("accounts.add_token")
            }}</NsButton>
          </cv-form>
          <h4 class="mg-bottom">{{ $t("accounts.access") }}</h4>
          <p class="mg-bottom">{{ $t("accounts.access_help") }}</p>
          <label class="bx--label" for="access-user">{{
            $t("accounts.username")
          }}</label>
          <select
            id="access-user"
            v-model="accessUser"
            class="bx--select-input mg-bottom"
            :disabled="locked"
          >
            <option
              v-for="user in users"
              :key="user.username"
              :value="user.username"
            >
              {{ user.username }}
            </option>
          </select>
          <div class="table-wrapper mg-bottom">
            <table class="bx--data-table bx--data-table--compact">
              <thead>
                <tr>
                  <th>{{ $t("accounts.topic") }}</th>
                  <th>{{ $t("accounts.permission") }}</th>
                  <th>{{ $t("accounts.actions") }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="grant in grants" :key="grant.topic">
                  <td>{{ grant.topic }}</td>
                  <td>{{ grant.permission }}</td>
                  <td>
                    <NsButton
                      kind="danger--ghost"
                      size="small"
                      :disabled="locked || grant.provisioned"
                      @click="removeGrant(grant)"
                      >{{ $t("accounts.remove") }}</NsButton
                    >
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <cv-form @submit.prevent="setAccess">
            <cv-text-input
              v-model.trim="topic"
              :label="$t('accounts.topic')"
              :disabled="locked || selectedAdmin"
              :invalid-message="topicError"
              class="mg-bottom"
            />
            <label class="bx--label" for="access-permission">{{
              $t("accounts.permission")
            }}</label>
            <select
              id="access-permission"
              v-model="permission"
              class="bx--select-input mg-bottom"
              :disabled="locked || selectedAdmin"
            >
              <option
                v-for="value in [
                  'read-write',
                  'read-only',
                  'write-only',
                  'deny',
                ]"
                :key="value"
                :value="value"
              >
                {{ value }}
              </option>
            </select>
            <NsButton
              kind="primary"
              :disabled="locked || !accessUser || selectedAdmin"
              >{{ $t("accounts.save_access") }}</NsButton
            >
          </cv-form>
          <NsButton kind="ghost" :disabled="busy" @click="refresh">{{
            $t("accounts.refresh")
          }}</NsButton>
        </cv-tile></cv-column
      ></cv-row
    >
  </cv-grid>
</template>

<script>
import { mapState } from "vuex";
import {
  QueryParamService,
  UtilService,
  TaskService,
  PageTitleService,
} from "@nethserver/ns8-ui-lib";

export default {
  name: "Accounts",
  mixins: [QueryParamService, UtilService, TaskService, PageTitleService],
  pageTitle() {
    return this.$t("accounts.title") + " - " + this.appName;
  },
  data() {
    return {
      q: { page: "accounts" },
      urlCheckInterval: null,
      users: [],
      tokens: [],
      busy: false,
      authEnabled: true,
      error: "",
      restartFailed: false,
      credential: null,
      visible: false,
      copied: false,
      username: "",
      role: "user",
      usernameError: "",
      tokenUser: "",
      label: "",
      expires: "never",
      accessUser: "",
      topic: "",
      topicError: "",
      permission: "read-write",
      taskListeners: [],
    };
  },
  computed: {
    ...mapState(["instanceName", "core", "appName"]),
    locked() {
      return this.busy || !this.authEnabled;
    },
    namedUsers() {
      return this.users.filter((user) => user.username !== "*");
    },
    grants() {
      const user = this.users.find((user) => user.username === this.accessUser);
      return user ? user.access : [];
    },
    selectedAdmin() {
      const user = this.users.find((user) => user.username === this.accessUser);
      return user && user.role === "admin";
    },
  },
  created() {
    this.refresh();
  },
  beforeRouteEnter(to, from, next) {
    next((vm) => {
      vm.watchQueryData(vm);
      vm.urlCheckInterval = vm.initUrlBindingForApp(vm, vm.q.page);
    });
  },
  beforeRouteLeave(to, from, next) {
    this.clearCredential();
    clearInterval(this.urlCheckInterval);
    next();
  },
  beforeDestroy() {
    this.taskListeners.forEach(([event, handler]) =>
      this.core.$root.$off(event, handler)
    );
    this.clearCredential();
  },
  methods: {
    task(action, data = {}) {
      return new Promise((resolve, reject) => {
        const eventId = this.getUuid();
        const listeners = [];
        const cleanup = () => {
          listeners.forEach(([event, handler]) =>
            this.core.$root.$off(event, handler)
          );
          this.taskListeners = this.taskListeners.filter(
            (item) => !listeners.includes(item)
          );
        };
        const listen = (suffix, callback) => {
          const event = `${action}-${suffix}-${eventId}`;
          const handler = (...args) => {
            cleanup();
            callback(...args);
          };
          const pair = [event, handler];
          listeners.push(pair);
          this.taskListeners.push(pair);
          this.core.$root.$once(event, handler);
        };
        listen("completed", (context, result) => resolve(result.output));
        listen("aborted", () => reject(new Error("failed")));
        listen("validation-failed", () => reject(new Error("failed")));
        this.createModuleTaskForApp(this.instanceName, {
          action,
          data,
          extra: {
            title: this.$t("accounts.title"),
            isNotificationHidden: true,
            eventId,
          },
        }).catch(() => {
          cleanup();
          reject(new Error("failed"));
        });
      });
    },
    clearCredential() {
      this.credential = null;
      this.visible = false;
      this.copied = false;
    },
    async copyCredential() {
      try {
        await navigator.clipboard.writeText(this.credential.value);
        this.copied = true;
      } catch {
        this.error = this.$t("accounts.copy_failed");
      }
    },
    isLastAdmin(user) {
      return (
        user.role === "admin" &&
        this.users.filter((user) => user.role === "admin").length <= 1
      );
    },
    async loadAccounts() {
      const result = await this.task("list-accounts");
      this.authEnabled = result.auth_enabled !== false;
      this.users = result.users;
      this.tokens = result.tokens;
      if (!this.namedUsers.some((user) => user.username === this.tokenUser))
        this.tokenUser = this.namedUsers[0]?.username || "";
      if (!this.users.some((user) => user.username === this.accessUser))
        this.accessUser = this.namedUsers[0]?.username || "*";
    },
    async refresh() {
      this.busy = true;
      this.error = "";
      try {
        await this.loadAccounts();
      } catch {
        this.error = this.$t("accounts.failed");
      } finally {
        this.busy = false;
      }
    },
    async mutate(data) {
      if (this.busy) return false;
      this.busy = true;
      this.error = "";
      this.restartFailed = false;
      this.clearCredential();
      try {
        const result = await this.task("manage-account", data);
        if (result.password || result.token)
          this.credential = {
            username: result.username,
            kind: result.password ? "password" : "token",
            value: result.password || result.token,
          };
        this.restartFailed = !!result.restart_failed;
        await this.loadAccounts();
        return true;
      } catch {
        this.error = this.$t("accounts.failed");
        return false;
      } finally {
        this.busy = false;
      }
    },
    async addUser() {
      this.usernameError = "";
      if (
        !/^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,127}$/.test(this.username) ||
        this.username === "everyone" ||
        this.users.some((user) => user.username === this.username)
      ) {
        this.usernameError = this.$t("accounts.invalid_username");
        return;
      }
      if (
        await this.mutate({
          operation: "add-user",
          username: this.username,
          role: this.role,
        })
      )
        this.username = "";
    },
    async addToken() {
      if (
        await this.mutate({
          operation: "add-token",
          username: this.tokenUser,
          label: this.label,
          expires: this.expires,
        })
      )
        this.label = "";
    },
    removeUser(user) {
      if (
        window.confirm(
          this.$t("accounts.confirm_remove", { name: user.username })
        )
      )
        this.mutate({ operation: "remove-user", username: user.username });
    },
    removeToken(token) {
      if (
        window.confirm(
          this.$t("accounts.confirm_remove", {
            name: token.label || token.username,
          })
        )
      )
        this.mutate({
          operation: "remove-token",
          username: token.username,
          id: token.id,
        });
    },
    removeGrant(grant) {
      if (
        window.confirm(
          this.$t("accounts.confirm_remove", { name: grant.topic })
        )
      )
        this.mutate({
          operation: "remove-access",
          username: this.accessUser,
          topic: grant.topic,
        });
    },
    setAccess() {
      this.topicError = "";
      if (!/^[A-Za-z0-9_*][A-Za-z0-9_.*-]{0,63}$/.test(this.topic)) {
        this.topicError = this.$t("accounts.invalid_topic");
        return;
      }
      this.mutate({
        operation: "set-access",
        username: this.accessUser,
        topic: this.topic,
        permission: this.permission,
      });
    },
  },
};
</script>

<style scoped lang="scss">
@import "../styles/carbon-utils";
.mg-bottom {
  margin-bottom: $spacing-06;
}
.table-wrapper {
  overflow-x: auto;
}
.credential {
  padding: $spacing-05;
  border: 1px solid #0f62fe;
}
.credential code {
  overflow-wrap: anywhere;
}
select {
  max-width: 32rem;
  display: block;
}
</style>
