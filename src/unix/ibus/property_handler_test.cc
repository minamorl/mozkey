// Copyright 2026 Mozkey contributors.
// SPDX-License-Identifier: BSD-3-Clause

#include "unix/ibus/property_handler.h"

#include <memory>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

#include "base/file_util.h"
#include "base/system_util.h"
#include "client/client.h"
#include "client/client_mock.h"
#include "protocol/commands.pb.h"
#include "testing/gmock.h"
#include "testing/gunit.h"
#include "testing/mozctest.h"
#include "unix/ibus/ibus_header.h"
#include "unix/ibus/ibus_wrapper.h"
#include "unix/ibus/message_translator.h"
#include "unix/ibus/mozc_engine.h"

namespace mozc::ibus {
namespace {

using ::testing::_;
using ::testing::Return;

class PropertyHandlerTest : public ::testing::Test {
 protected:
  void SetUp() override {
    EXPECT_CALL(client_, SendCommandWithContext(_, _, _))
        .WillOnce([](const commands::SessionCommand& command,
                     const commands::Context&, commands::Output*) {
          EXPECT_EQ(command.type(), commands::SessionCommand::TURN_ON_IME);
          return true;
        });
    handler_ = std::make_unique<PropertyHandler>(
        std::make_unique<NullMessageTranslator>(), true, &client_);
  }

  client::ClientMock client_;
  std::unique_ptr<PropertyHandler> handler_;
};

TEST_F(PropertyHandlerTest, DirectModeReturnsCommitAndEmptyComposition) {
  commands::Output response;
  response.set_consumed(true);
  response.mutable_status()->set_activated(false);
  response.mutable_status()->set_mode(commands::HIRAGANA);
  response.mutable_result()->set_type(commands::Result::STRING);
  response.mutable_result()->set_value("日本語");
  ASSERT_TRUE(response.IsInitialized());
  EXPECT_CALL(client_, SendCommandWithContext(_, _, _))
      .WillOnce([&](const commands::SessionCommand& command,
                    const commands::Context&, commands::Output* output) {
        EXPECT_EQ(command.type(), commands::SessionCommand::TURN_OFF_IME);
        EXPECT_EQ(command.composition_mode(), commands::HIRAGANA);
        *output = response;
        return true;
      });

  const auto output =
      handler_->ProcessPropertyActivate("InputMode.Direct", PROP_STATE_CHECKED);
  ASSERT_TRUE(output.has_value());
  EXPECT_EQ(output->SerializeAsString(), response.SerializeAsString());
  EXPECT_FALSE(output->has_preedit());
  EXPECT_FALSE(output->has_candidate_window());
}

TEST_F(PropertyHandlerTest, OtherModeReturnsPreeditAndCandidates) {
  commands::Output response;
  response.set_consumed(true);
  response.mutable_status()->set_activated(true);
  response.mutable_status()->set_mode(commands::FULL_KATAKANA);
  response.mutable_preedit()->set_cursor(4);
  auto* segment = response.mutable_preedit()->add_segment();
  segment->set_annotation(commands::Preedit::Segment::UNDERLINE);
  segment->set_value("ニホンゴ");
  segment->set_value_length(4);
  auto* window = response.mutable_candidate_window();
  window->set_size(1);
  window->set_position(0);
  auto* candidate = window->add_candidate();
  candidate->set_index(0);
  candidate->set_value("日本語");
  ASSERT_TRUE(response.IsInitialized());
  EXPECT_CALL(client_, SendCommandWithContext(_, _, _))
      .WillOnce([&](const commands::SessionCommand& command,
                    const commands::Context&, commands::Output* output) {
        EXPECT_EQ(command.type(),
                  commands::SessionCommand::SWITCH_COMPOSITION_MODE);
        EXPECT_EQ(command.composition_mode(), commands::FULL_KATAKANA);
        *output = response;
        return true;
      });

  const auto output = handler_->ProcessPropertyActivate("InputMode.Katakana",
                                                        PROP_STATE_CHECKED);
  ASSERT_TRUE(output.has_value());
  EXPECT_EQ(output->SerializeAsString(), response.SerializeAsString());
}

TEST_F(PropertyHandlerTest, IgnoredPropertiesDoNotProduceAnUpdate) {
  EXPECT_CALL(client_, SendCommandWithContext(_, _, _)).Times(0);
  EXPECT_FALSE(handler_->ProcessPropertyActivate("InputMode.Direct",
                                                 PROP_STATE_UNCHECKED));
  EXPECT_FALSE(
      handler_->ProcessPropertyActivate("Unknown", PROP_STATE_CHECKED));
}

TEST_F(PropertyHandlerTest, FailedCommandDoesNotApplyPartialResponse) {
  EXPECT_CALL(client_, SendCommandWithContext(_, _, _))
      .WillOnce([](const commands::SessionCommand&, const commands::Context&,
                   commands::Output* output) {
        output->mutable_status()->set_activated(false);
        output->mutable_result()->set_value("partial");
        return false;
      });
  EXPECT_FALSE(handler_->ProcessPropertyActivate("InputMode.Direct",
                                                 PROP_STATE_CHECKED));
  EXPECT_TRUE(handler_->IsActivated());
}

TEST_F(PropertyHandlerTest, MissingStatusDoesNotProduceAnUpdate) {
  EXPECT_CALL(client_, SendCommandWithContext(_, _, _)).WillOnce(Return(true));
  EXPECT_FALSE(handler_->ProcessPropertyActivate("InputMode.Direct",
                                                 PROP_STATE_CHECKED));
  EXPECT_TRUE(handler_->IsActivated());
}

TEST_F(PropertyHandlerTest,
       TransportSuccessWithUndoOnlyStatusIsNotModeSuccess) {
  // SessionHandler can return true after Session rejected the command, with
  // exactly this partial status supplied by MaybeSetUndoStatus.
  EXPECT_CALL(client_, SendCommandWithContext(_, _, _))
      .WillOnce([](const commands::SessionCommand&, const commands::Context&,
                   commands::Output* output) {
        output->mutable_status()->set_undo_available(true);
        return true;
      });
  EXPECT_FALSE(handler_->ProcessPropertyActivate("InputMode.Direct",
                                                 PROP_STATE_CHECKED));
  EXPECT_TRUE(handler_->IsActivated());
  EXPECT_EQ(handler_->GetOriginalCompositionMode(), commands::HIRAGANA);
}

commands::Output ModeResponse(bool activated, commands::CompositionMode mode) {
  commands::Output output;
  output.set_consumed(true);
  output.set_mode(activated ? mode : commands::DIRECT);
  output.mutable_status()->set_activated(activated);
  output.mutable_status()->set_mode(mode);
  return output;
}

// Observe the real IbusEngineWrapper -> IBusEngine -> GDBus output. No owner
// bus, daemon, display or environment is used. The only endpoint is a new
// random Linux abstract socket, restricted to EXTERNAL authentication by the
// same user.
class PropertyDispatchTest : public mozc::testing::TestWithTempUserProfile,
                             public client::ClientFactoryInterface {
 protected:
  struct Signal {
    std::string member;
    std::shared_ptr<GVariant> body;
  };
  struct SignalLog {
    std::mutex mutex;
    std::vector<Signal> signals;
    guint barrier = 0;
  };
  struct ConnectResult {
    GDBusConnection* client = nullptr;
    GError* error = nullptr;
    bool done = false;
    ~ConnectResult() {
      g_clear_object(&client);
      g_clear_error(&error);
    }
  };

  template <typename Predicate>
  bool WaitUntil(Predicate done) {
    const gint64 deadline = g_get_monotonic_time() + 5 * G_TIME_SPAN_SECOND;
    while (!done() && g_get_monotonic_time() < deadline) {
      g_main_context_iteration(context_, FALSE);
      g_usleep(1000);
    }
    return done();
  }

  void SetUp() override {
    ASSERT_OK(FileUtil::SetContents(
        FileUtil::JoinPath(SystemUtil::GetUserProfileDirectory(),
                           "ibus_config.textproto"),
        "active_on_launch: true\nmozc_renderer { enabled: false }\n"));
    client::ClientFactory::SetClientFactory(this);
    engine_ = std::make_unique<MozcEngine>();

    context_ = g_main_context_new();
    g_main_context_push_thread_default(context_);
    cancellable_ = g_cancellable_new();
    auth_ = g_dbus_auth_observer_new();
    g_signal_connect(auth_, "allow-mechanism",
                     G_CALLBACK(+[](GDBusAuthObserver*, const gchar* mechanism,
                                    gpointer) -> gboolean {
                       return g_strcmp0(mechanism, "EXTERNAL") == 0;
                     }),
                     nullptr);
    g_signal_connect(
        auth_, "authorize-authenticated-peer",
        G_CALLBACK(+[](GDBusAuthObserver*, GIOStream*,
                       GCredentials* credentials, gpointer) -> gboolean {
          GCredentials* self = g_credentials_new();
          const bool same =
              credentials != nullptr &&
              g_credentials_is_same_user(self, credentials, nullptr);
          g_object_unref(self);
          return same;
        }),
        nullptr);
    gchar* guid = g_dbus_generate_guid();
    guid_ = guid;
    const std::string socket_name = std::string("mozkey-property-") + guid;
    const std::string address = "unix:abstract=" + socket_name;
    g_free(guid);
    // GDBusServer constructs accepted connections on a worker thread, whose
    // default context would also need draining. Construct both peers here.
    server_ = g_socket_service_new();
    GSocketAddress* socket_address = g_unix_socket_address_new_with_type(
        socket_name.c_str(), -1, G_UNIX_SOCKET_ADDRESS_ABSTRACT);
    GError* error = nullptr;
    const bool listening = g_socket_listener_add_address(
        G_SOCKET_LISTENER(server_), socket_address, G_SOCKET_TYPE_STREAM,
        G_SOCKET_PROTOCOL_DEFAULT, nullptr, nullptr, &error);
    g_object_unref(socket_address);
    if (error != nullptr) {
      ADD_FAILURE() << "Private D-Bus setup failed, code=" << error->code;
      g_error_free(error);
    }
    ASSERT_TRUE(listening);
    accepted_ = std::make_shared<ConnectResult>();
    g_signal_connect(
        server_, "incoming",
        G_CALLBACK(+[](GSocketService*, GSocketConnection* socket, GObject*,
                       gpointer data) -> gboolean {
          auto* test = static_cast<PropertyDispatchTest*>(data);
          if (test->accept_started_) return FALSE;
          test->accept_started_ = true;
          g_dbus_connection_new(
              G_IO_STREAM(socket), test->guid_.c_str(),
              G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_SERVER, test->auth_,
              test->cancellable_,
              +[](GObject*, GAsyncResult* result, gpointer data) {
                std::unique_ptr<std::shared_ptr<ConnectResult>> state(
                    static_cast<std::shared_ptr<ConnectResult>*>(data));
                (*state)->client =
                    g_dbus_connection_new_finish(result, &(*state)->error);
                (*state)->done = true;
              },
              new std::shared_ptr<ConnectResult>(test->accepted_));
          return TRUE;
        }),
        this);
    g_socket_service_start(server_);
    connected_ = std::make_shared<ConnectResult>();
    g_dbus_connection_new_for_address(
        address.c_str(), G_DBUS_CONNECTION_FLAGS_AUTHENTICATION_CLIENT, nullptr,
        cancellable_,
        +[](GObject*, GAsyncResult* result, gpointer data) {
          std::unique_ptr<std::shared_ptr<ConnectResult>> state(
              static_cast<std::shared_ptr<ConnectResult>*>(data));
          (*state)->client = g_dbus_connection_new_for_address_finish(
              result, &(*state)->error);
          (*state)->done = true;
        },
        new std::shared_ptr<ConnectResult>(connected_));
    ASSERT_TRUE(WaitUntil([&] { return connected_->done; }));
    ASSERT_EQ(connected_->error, nullptr);
    ASSERT_NE(connected_->client, nullptr);
    ASSERT_TRUE(WaitUntil([&] { return accepted_->done; }));
    ASSERT_EQ(accepted_->error, nullptr);
    connection_ = std::exchange(accepted_->client, nullptr);
    ASSERT_NE(connection_, nullptr);

    log_ = std::make_shared<SignalLog>();
    filter_ = g_dbus_connection_add_filter(
        connection_,
        +[](GDBusConnection*, GDBusMessage* message, gboolean incoming,
            gpointer data) -> GDBusMessage* {
          if (incoming ||
              g_dbus_message_get_message_type(message) !=
                  G_DBUS_MESSAGE_TYPE_SIGNAL ||
              g_strcmp0(g_dbus_message_get_interface(message),
                        IBUS_INTERFACE_ENGINE) != 0)
            return message;
          auto log = *static_cast<std::shared_ptr<SignalLog>*>(data);
          std::lock_guard<std::mutex> lock(log->mutex);
          const std::string member = g_dbus_message_get_member(message);
          GVariant* body = g_dbus_message_get_body(message);
          if (member == "TestBarrier") {
            g_variant_get(body, "(u)", &log->barrier);
          } else {
            log->signals.push_back(
                {member, body ? std::shared_ptr<GVariant>(g_variant_ref(body),
                                                          g_variant_unref)
                              : nullptr});
          }
          return message;
        },
        new std::shared_ptr<SignalLog>(log_),
        +[](gpointer data) {
          delete static_cast<std::shared_ptr<SignalLog>*>(data);
        });
    ibus_engine_ = ibus_engine_new("property-test", kPath, connection_);
    ASSERT_NE(ibus_engine_, nullptr);
    g_object_ref_sink(ibus_engine_);
    ibus_engine_->client_capabilities = 0;  // No surrounding-text requests.
    wrapper_ = std::make_unique<IbusEngineWrapper>(ibus_engine_);
  }

  void TearDown() override {
    wrapper_.reset();
    if (ibus_engine_) {
      ibus_object_destroy(IBUS_OBJECT(ibus_engine_));
      g_clear_object(&ibus_engine_);
    }
    if (server_) {
      g_signal_handlers_disconnect_by_data(server_, this);
      g_socket_service_stop(server_);
      g_socket_listener_close(G_SOCKET_LISTENER(server_));
    }
    if (cancellable_) g_cancellable_cancel(cancellable_);
    if (context_) {
      EXPECT_TRUE(WaitUntil([&] {
        return (!connected_ || connected_->done) &&
               (!accept_started_ || accepted_->done);
      })) << "Private connection setup cancellation timed out";
    }
    // Setup may have failed after accepting but before transferring this ref.
    if (!connection_ && accepted_) {
      connection_ = std::exchange(accepted_->client, nullptr);
    }
    if (filter_) g_dbus_connection_remove_filter(connection_, filter_);
    filter_ = 0;

    GWeakRef server_peer, client_peer, listener;
    g_weak_ref_init(&server_peer, connection_);
    g_weak_ref_init(&client_peer, connected_ ? connected_->client : nullptr);
    g_weak_ref_init(&listener, server_);
    std::weak_ptr<SignalLog> weak_log = log_;
    std::weak_ptr<ConnectResult> weak_connected = connected_;
    std::weak_ptr<ConnectResult> weak_accepted = accepted_;
    auto pending_closes = std::make_shared<int>(0);
    const auto close = [&](GDBusConnection* connection) {
      if (!connection || g_dbus_connection_is_closed(connection)) return;
      ++*pending_closes;
      g_dbus_connection_close(
          connection, nullptr,
          +[](GObject* object, GAsyncResult* result, gpointer data) {
            std::unique_ptr<std::shared_ptr<int>> pending(
                static_cast<std::shared_ptr<int>*>(data));
            GError* error = nullptr;
            const bool closed = g_dbus_connection_close_finish(
                G_DBUS_CONNECTION(object), result, &error);
            EXPECT_TRUE(closed ||
                        g_error_matches(error, G_IO_ERROR, G_IO_ERROR_CLOSED));
            g_clear_error(&error);
            --**pending;
          },
          new std::shared_ptr<int>(pending_closes));
    };
    close(connection_);
    close(connected_ ? connected_->client : nullptr);
    g_clear_object(&connection_);
    connected_.reset();
    accepted_.reset();
    log_.reset();
    g_clear_object(&server_);
    g_clear_object(&auth_);
    g_clear_object(&cancellable_);
    const auto gone = [](GWeakRef* weak) {
      gpointer object = g_weak_ref_get(weak);
      const bool released = object == nullptr;
      if (object) g_object_unref(object);
      return released;
    };
    if (context_) {
      // close completion alone does not dispatch the queued ::closed signal or
      // filter destroy notify. Prove their refs are gone before dropping
      // context.
      EXPECT_TRUE(WaitUntil([&] {
        return *pending_closes == 0 && gone(&server_peer) &&
               gone(&client_peer) && gone(&listener) && weak_log.expired() &&
               weak_connected.expired() && weak_accepted.expired() &&
               !g_main_context_pending(context_);
      })) << "Private D-Bus cleanup did not release all references";
      g_main_context_pop_thread_default(context_);
      g_main_context_unref(context_);
      context_ = nullptr;
    }
    g_weak_ref_clear(&server_peer);
    g_weak_ref_clear(&client_peer);
    g_weak_ref_clear(&listener);
    engine_.reset();
    client::ClientFactory::SetClientFactory(nullptr);
  }

  std::unique_ptr<client::ClientInterface> NewClient() override {
    auto mock = std::make_unique<::testing::StrictMock<client::ClientMock>>();
    client_ = mock.get();
    EXPECT_CALL(*client_, set_client_capability(_));
    EXPECT_CALL(*client_, SendCommandWithContext(_, _, _))
        .WillOnce([](const commands::SessionCommand& command,
                     const commands::Context&, commands::Output*) {
          EXPECT_EQ(command.type(), commands::SessionCommand::TURN_ON_IME);
          return true;
        });
    EXPECT_CALL(*client_, LaunchToolWithProtoBuf(_))
        .WillRepeatedly(Return(false));
    EXPECT_CALL(*client_, SyncData()).WillOnce(Return(true));
    return mock;
  }

  std::vector<Signal> Drain() {
    // A marker behind engine messages on the same outgoing queue makes absence
    // assertions deterministic, including when the engine emits nothing.
    const guint barrier = ++barrier_;
    EXPECT_TRUE(g_dbus_connection_emit_signal(
        connection_, nullptr, kPath, IBUS_INTERFACE_ENGINE, "TestBarrier",
        g_variant_new("(u)", barrier), nullptr));
    EXPECT_TRUE(WaitUntil([&] {
      std::lock_guard<std::mutex> lock(log_->mutex);
      return log_->barrier == barrier;
    }));
    std::lock_guard<std::mutex> lock(log_->mutex);
    return std::exchange(log_->signals, {});
  }

  static int Count(const std::vector<Signal>& signals, const char* member) {
    int count = 0;
    for (const auto& signal : signals) count += signal.member == member;
    return count;
  }

  static std::string Text(GVariant* body) {
    GVariant* value = nullptr;
    g_variant_get_child(body, 0, "v", &value);
    IBusSerializable* object = ibus_serializable_deserialize(value);
    g_variant_unref(value);
    if (!object || !IBUS_IS_TEXT(object)) {
      ADD_FAILURE() << "Expected serialized IBusText";
      if (object) g_object_unref(object);
      return {};
    }
    const std::string text = ibus_text_get_text(IBUS_TEXT(object));
    g_object_unref(object);
    return text;
  }

  void PrimeComposition() {
    auto response = ModeResponse(true, commands::FULL_KATAKANA);
    auto* preedit = response.mutable_preedit();
    preedit->set_cursor(4);
    auto* segment = preedit->add_segment();
    segment->set_annotation(commands::Preedit::Segment::UNDERLINE);
    segment->set_value("ニホンゴ");
    segment->set_value_length(4);
    auto* window = response.mutable_candidate_window();
    window->set_size(1);
    window->set_position(0);
    auto* candidate = window->add_candidate();
    candidate->set_index(0);
    candidate->set_value("日本語");
    ASSERT_TRUE(response.IsInitialized());
    EXPECT_CALL(*client_, SendKeyWithContext(_, _, _))
        .WillOnce([&](const commands::KeyEvent&, const commands::Context&,
                      commands::Output* output) {
          *output = response;
          return true;
        });
    ASSERT_TRUE(engine_->ProcessKeyEvent(wrapper_.get(), 'a', 30, 0));
    const auto signals = Drain();
    ASSERT_EQ(Count(signals, "UpdatePreeditText"), 1);
    ASSERT_EQ(Count(signals, "UpdateLookupTable"), 1);
    for (const auto& signal : signals) {
      if (signal.member == "UpdatePreeditText") {
        EXPECT_EQ(Text(signal.body.get()), "ニホンゴ");
        gboolean visible = FALSE;
        g_variant_get_child(signal.body.get(), 2, "b", &visible);
        EXPECT_TRUE(visible);
      } else if (signal.member == "UpdateLookupTable") {
        gboolean visible = FALSE;
        g_variant_get_child(signal.body.get(), 1, "b", &visible);
        EXPECT_TRUE(visible);
      }
    }
  }

  void ExpectDirectResponse(const commands::Output& response,
                            bool transport = true) {
    EXPECT_CALL(*client_, SendCommandWithContext(_, _, _))
        .WillOnce([response, transport](const commands::SessionCommand& command,
                                        const commands::Context&,
                                        commands::Output* output) {
          EXPECT_EQ(command.type(), commands::SessionCommand::TURN_OFF_IME);
          EXPECT_EQ(command.composition_mode(), commands::FULL_KATAKANA);
          *output = response;
          return transport;
        });
  }

  void ExpectCommittedAndCleared(const std::vector<Signal>& signals) {
    EXPECT_EQ(Count(signals, "CommitText"), 1);
    EXPECT_EQ(Count(signals, "UpdatePreeditText"), 1);
    EXPECT_EQ(Count(signals, "HidePreeditText"), 1);
    EXPECT_EQ(Count(signals, "HideLookupTable"), 1);
    bool direct_checked = false;
    for (const auto& signal : signals) {
      if (signal.member == "CommitText")
        EXPECT_EQ(Text(signal.body.get()), "日本語");
      if (signal.member == "UpdatePreeditText") {
        EXPECT_EQ(Text(signal.body.get()), "");
        gboolean visible = TRUE;
        guint mode = IBUS_ENGINE_PREEDIT_COMMIT;
        g_variant_get_child(signal.body.get(), 2, "b", &visible);
        g_variant_get_child(signal.body.get(), 3, "u", &mode);
        EXPECT_FALSE(visible);
        EXPECT_EQ(mode, IBUS_ENGINE_PREEDIT_CLEAR);
      }
      if (signal.member == "UpdateProperty") {
        GVariant* value = nullptr;
        g_variant_get(signal.body.get(), "(v)", &value);
        IBusSerializable* object = ibus_serializable_deserialize(value);
        g_variant_unref(value);
        ASSERT_TRUE(object && IBUS_IS_PROPERTY(object));
        auto* property = IBUS_PROPERTY(object);
        if (std::string(ibus_property_get_key(property)) ==
            "InputMode.Direct") {
          direct_checked =
              ibus_property_get_state(property) == PROP_STATE_CHECKED;
        }
        g_object_unref(object);
      }
    }
    EXPECT_TRUE(direct_checked);
  }

  static commands::Output CommitResponse() {
    auto response = ModeResponse(false, commands::FULL_KATAKANA);
    response.mutable_status()->set_undo_available(true);
    response.mutable_result()->set_type(commands::Result::STRING);
    response.mutable_result()->set_value("日本語");
    return response;
  }

  static constexpr char kPath[] = "/org/freedesktop/IBus/PropertyTest";
  std::unique_ptr<MozcEngine> engine_;
  client::ClientMock* client_ = nullptr;
  std::unique_ptr<IbusEngineWrapper> wrapper_;
  GMainContext* context_ = nullptr;
  GCancellable* cancellable_ = nullptr;
  GSocketService* server_ = nullptr;
  GDBusAuthObserver* auth_ = nullptr;
  std::string guid_;
  bool accept_started_ = false;
  GDBusConnection* connection_ = nullptr;
  IBusEngine* ibus_engine_ = nullptr;
  guint filter_ = 0;
  guint barrier_ = 0;
  std::shared_ptr<ConnectResult> connected_;
  std::shared_ptr<ConnectResult> accepted_;
  std::shared_ptr<SignalLog> log_;
};

TEST_F(PropertyDispatchTest, DirectCommitsOnceClearsUIAndIgnoresDuplicate) {
  ASSERT_NO_FATAL_FAILURE(PrimeComposition());
  ExpectDirectResponse(CommitResponse());
  engine_->PropertyActivate(wrapper_.get(), "InputMode.Direct",
                            PROP_STATE_CHECKED);
  ExpectCommittedAndCleared(Drain());
  // This must send neither unsupported SWITCH_COMPOSITION_MODE(DIRECT) nor a
  // second commit. StrictMock also catches an unexpected second command.
  engine_->PropertyActivate(wrapper_.get(), "InputMode.Direct",
                            PROP_STATE_CHECKED);
  EXPECT_TRUE(Drain().empty());
}

TEST_F(PropertyDispatchTest,
       PartialRejectedAndFailedResponsesLeaveUIAndModeAlone) {
  ASSERT_NO_FATAL_FAILURE(PrimeComposition());
  std::vector<commands::Output> invalid;
  commands::Output undo_only;
  undo_only.mutable_status()->set_undo_available(true);
  invalid.push_back(undo_only);
  auto response = CommitResponse();
  response.clear_consumed();
  invalid.push_back(response);
  response = CommitResponse();
  response.set_consumed(false);
  invalid.push_back(response);
  response = CommitResponse();
  response.mutable_status()->clear_mode();
  invalid.push_back(response);
  response = CommitResponse();
  response.mutable_status()->clear_activated();
  invalid.push_back(response);
  response = CommitResponse();
  response.mutable_status()->set_activated(true);
  invalid.push_back(response);
  response = CommitResponse();
  response.mutable_status()->set_mode(commands::HIRAGANA);
  invalid.push_back(response);
  response = CommitResponse();
  response.mutable_result()->clear_type();
  invalid.push_back(response);
  response = CommitResponse();
  response.set_error_code(commands::Output::SESSION_FAILURE);
  invalid.push_back(response);
  int case_index = 0;
  for (const auto& partial : invalid) {
    SCOPED_TRACE(case_index++);
    ExpectDirectResponse(partial);
    engine_->PropertyActivate(wrapper_.get(), "InputMode.Direct",
                              PROP_STATE_CHECKED);
    EXPECT_TRUE(Drain().empty());
  }
  ExpectDirectResponse(CommitResponse(), false);
  engine_->PropertyActivate(wrapper_.get(), "InputMode.Direct",
                            PROP_STATE_CHECKED);
  EXPECT_TRUE(Drain().empty());
  // Every retry must still carry the saved Katakana mode. Only this successful
  // reply may commit, clear composition and switch the Direct property.
  ExpectDirectResponse(CommitResponse());
  engine_->PropertyActivate(wrapper_.get(), "InputMode.Direct",
                            PROP_STATE_CHECKED);
  ExpectCommittedAndCleared(Drain());
}

}  // namespace
}  // namespace mozc::ibus
