import Skype4Py
import time
import pkgutil
import datetime
import json
import sys
import logging
import twitterconnector
from hookserver import HookServerMessage
import queuedthread
import time
from messages import housekeeping, streetnoise
import re
import os
import commands

class ChatHandler(object):
    
    def __init__( self, Chat ):
        self.chat = Chat
        self.last_timestamp = datetime.datetime.now()

    def update( self ):
        new_messages = []
        messages = self.chat.RecentMessages
        for message in messages:
            dt = message.Datetime
            if dt > self.last_timestamp:
                new_messages.append( message )
                self.last_timestamp = dt
                #print message.Id
        return new_messages

RUN_SKYPE = True
ENABLE_TWITTER = True
ENABLE_GIFTS = True
class BotThread( queuedthread.QueuedThread ):
    
    def __init__( self ):
        self.twitter_connector = None
        super( BotThread, self ).__init__()

    def message_all( self, message ):
    # send message to all connected chats   
        for chat_name in self.chat_handlers:
            chat_handler = self.chat_handlers[ chat_name ]
            try:
                chat_handler.chat.SendMessage( message )
            except Exception, e:
                logging.info( e )

    def stop( self, message=None ):
        if self.twitter_connector:
            self.twitter_connector.stop()
        if message is not None:
            self.message_all( message )
        super( BotThread, self ).stop()

    def run( self ):
        self._abortflag = False
        
        # twitter connection
        if ENABLE_TWITTER:
            logging.info( "Starting up Twitter connector..." )
            self.twitter_connector = twitterconnector.TwitterConnectorThread()
            self.twitter_connector.creds_path = "twitter_creds"
            self.twitter_connector.track_keywords = ["lndlrd"]
            self.twitter_connector.start()

        # import commands
        all_commands = []
        logging.info( "Loading commands..." )
        for loader, modname, ispkg in pkgutil.iter_modules( commands.__path__, prefix="commands." ):
            try:
                module = __import__( modname, fromlist="dummy" )
                for klassname in dir( module ):
                    if "Command" in klassname:
                        logging.info( "Instantiate command: %s" % klassname )
                        kommandklass = getattr( module, klassname )
                        all_commands.append( kommandklass() )
            except Exception, e:
                logging.info( e )

        if RUN_SKYPE:
            logging.info( "Attaching to Skype..." )
            skype = Skype4Py.Skype(Transport='x11')
            skype.Attach()
        
        self.chat_handlers = {}
        logging.info( "Entering main run loop..." )
        while not self._abortflag:
            try:
                if RUN_SKYPE:
                    # maintain list of chats
                    chats = skype.ActiveChats
                    defunct_chat_names = set( self.chat_handlers.keys() )
                    for chat in chats:
                        chat_name = chat.Name
                        try: 
                            defunct_chat_names.remove( chat_name )
                        except KeyError:
                            pass
                        if chat_name not in self.chat_handlers:
                            logging.info( "New handler for chat: %s" % chat.FriendlyName )
                            self.chat_handlers[chat_name] = ChatHandler(chat)
                            message = housekeeping.new_chat_message()
                            try:
                                chat.SendMessage( message )
                            except Exception, e:
                                logging.info( e )
                                print e

                    # clear defunct chats
                    for defunct_chat_name in defunct_chat_names:
                        logging.info( "Delete handler for chat: %s" % defunct_chat_name )
                        del self.chat_handlers[ defunct_chat_name ]
                    
                    # update chats
                    for chat_name in self.chat_handlers:
                        chat_handler = self.chat_handlers[ chat_name ]
                        new_messages = chat_handler.update()
                        if len(new_messages)> 0:
                            print "New messages in chat: %s" % chat_handler.chat.FriendlyName
                        for new_message in new_messages:
                            body = new_message.Body
                            print body
                            bl = body.lower()
                            for command in all_commands:
                                try:
                                    if command.enabled:
                                        message_out = None
                                        for commandstring in command.command_mappings:
                                            commandbang = "!" + commandstring
                                            if commandbang in bl:
                                                # if command is giftable
                                                if ENABLE_GIFTS:
                                                    if hasattr( command, 'gift' ):
                                                        # split message up into tokens
                                                        tokens = re.split( '\W+', body )
                                                        print tokens
                                                        if len( tokens ) > 1:
                                                            members = chat_handler.chat.Members
                                                            # scan tokens for something that looks like a name
                                                            for token in tokens:
                                                                if len(token) > 3 and token != commandstring:
                                                                    for member in members:
                                                                        names = [ member.DisplayName, member.FullName, member.Handle ]
                                                                        for name in names:
                                                                            if token.lower() in name.lower():
                                                                                print "-->  gift %s to %s " % (commandbang, name )
                                                                                message_out = command.gift( name )
                                                                                break
                                                                        if message_out is not None:
                                                                            break
                                                                if message_out is not None:
                                                                        break
                                                if message_out is None:
                                                    message_out = command.execute( new_message )

                                    if message_out is not None:
                                        chat_handler.chat.SendMessage( message_out )
                                        if ENABLE_TWITTER and command.tweets:
                                            self.twitter_connector.tweet( message_out )

                                except Exception, e:
                                    logging.info( e )
                                    print e
                    
                    # update from twitter
                    if ENABLE_TWITTER:
                        new_statuses = self.twitter_connector.pop_stream()
                        for status_in in new_statuses:
                            try:
                                message_out = streetnoise.message_for_incoming_status( status_in )
                                if message_out:
                                    self.message_all( message_out )
                            except Exception, e:
                                logging.info( e )
                            
                    time.sleep(1)
            except Exception, e:
                logging.info( e )
                print e

        if ENABLE_TWITTER:
            self.twitter_connector.stop()
